/**
 * Third-person 3D view of the simulated arena.
 *
 * The camera panel shows what the drone sees; this shows *where the drone is*,
 * which is the half a student cannot reconstruct from a first-person feed. It is
 * driven entirely by `{"type":"scene"}` (once) and `{"type":"pose"}` (~30 Hz)
 * messages — it never touches the block program or the detector, and it is
 * strictly a display: the arena coordinates it draws with are not reachable from
 * any block (requirements §4).
 *
 * Adapters with no arena-absolute pose (mock, real Tello) send `scene: null`,
 * and the stage shows a placeholder instead.
 *
 * Coordinates. The simulator uses x east / y north / z up, heading clockwise
 * from +y. Three.js is y-up, so:  three.x = x,  three.y = z,  three.z = -y,
 * and a heading of h degrees is a rotation of -h about Y.
 */
import * as THREE from "three";
import { OrbitControls } from "./vendor/OrbitControls.js";

const DRONE_SPAN_M = 0.45;   // a Tello is ~0.18 m; drawn larger or it is a speck
const TRAIL_MAX = 400;
const PROP_SPIN = 25;        // rad/s while flying
const COL = {
  bg: 0x0b1220, floor: 0x27364f, grid: 0x3b4e78, gridMajor: 0x5b74ac,
  wall: 0x7f9bd4, body: 0x1b2333, arm: 0xf7941d, prop: 0xc7d3ea,
  fire: 0xdc2626, post: 0x475569, trail: 0x38bdf8, fov: 0x38bdf8,
};
const MARKER_COL = {
  // `destination` is the same red as a target on purpose — the camera cannot
  // tell them apart, and a 3D view that could would be teaching the wrong thing.
  fire: COL.fire, destination: COL.fire, red_square: COL.fire,
  blue_circle: 0x2563eb, green_triangle: 0x16a34a, yellow_square: 0xeab308,
  // Solid obstacles carry the same colours as the wall decorations of the same
  // name — they look identical to the camera, and the 3D view must not give
  // away which ones are solid any more than it gives away the destination.
  obstacle_red_square: COL.fire, obstacle_green_triangle: 0x16a34a,
  obstacle_blue_circle: 0x2563eb, obstacle_yellow_square: 0xeab308,
};

const deg = (d) => (d * Math.PI) / 180;
const toThree = (x, y, z) => new THREE.Vector3(x, z, -y);

/** Move `from` a fraction `k` towards `to` the short way round a 360 deg circle. */
function chaseAngle(from, to, k) {
  const delta = ((to - from + 540) % 360) - 180;
  return (from + delta * k + 360) % 360;
}

export function createStage(container, { hfovDeg = 70, aspect = 4 / 3 } = {}) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(COL.bg);
  scene.fog = new THREE.Fog(COL.bg, 10, 34);

  const camera = new THREE.PerspectiveCamera(52, 1, 0.05, 200);
  camera.position.set(3, 4, 6);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  container.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.maxPolarAngle = Math.PI / 2 - 0.02;
  controls.enabled = false;

  scene.add(new THREE.HemisphereLight(0x9fc0ff, 0x121a2c, 1.1));
  const sun = new THREE.DirectionalLight(0xffffff, 1.5);
  sun.position.set(4, 9, 5);
  sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024);
  Object.assign(sun.shadow.camera, { left: -8, right: 8, top: 8, bottom: -8, far: 30 });
  sun.shadow.camera.updateProjectionMatrix();
  scene.add(sun);
  scene.add(sun.target);

  const arena = new THREE.Group();
  scene.add(arena);
  const drone = buildDrone(hfovDeg, aspect);
  drone.group.visible = false;
  scene.add(drone.group);
  // the blob shadow lives in world space, not on the drone, so it stays flat on
  // the floor while the airframe banks
  drone.shadow.visible = false;
  scene.add(drone.shadow);

  const trail = buildTrail();
  scene.add(trail.line);

  // Pose arrives in discrete network updates; the renderer runs at display rate
  // and chases them, which is what turns 30 Hz of samples into smooth flight.
  const target = { x: 0, y: 0, z: 0, heading: 0, roll: 0, pitch: 0, flying: false };
  const shown = { ...target };
  let started = false;
  let mode = "follow";
  let showTrail = true;
  let arenaW = 4, arenaD = 4;
  let billboards = [];             // marker faces that turn to face the camera
  const clock = new THREE.Clock();

  function setScene(desc) {
    disposeChildren(arena);          // a reconnect rebuilds the arena from scratch
    trail.reset();
    billboards = [];
    if (!desc) { drone.group.visible = drone.shadow.visible = false; return; }
    arenaW = desc.width_m ?? desc.size_m;
    arenaD = desc.depth_m ?? desc.size_m;
    billboards = buildArena(arena, desc);
    // fog only for the void beyond the arena — pulled in any closer it greys out
    // the room itself, worst of all from the top-down camera
    const span = Math.max(arenaW, arenaD);
    scene.fog.near = span * 2.5;
    scene.fog.far = span * 9;
    // The sun's shadow frustum is a fixed box, so it has to be resized to the
    // room — left at the square arena's size, a 10 m corridor loses every
    // shadow past the halfway point.
    sun.position.set(arenaW / 2 + span * 0.35, span * 1.4, -arenaD / 2 + span * 0.45);
    sun.target.position.set(arenaW / 2, 0, -arenaD / 2);
    sun.target.updateMatrixWorld();
    Object.assign(sun.shadow.camera,
                  { left: -span, right: span, top: span, bottom: -span, far: span * 5 });
    sun.shadow.camera.updateProjectionMatrix();
    drone.group.visible = drone.shadow.visible = true;
    const [sx, sy] = desc.start ?? [arenaW / 2, arenaD / 2];
    Object.assign(target, { x: sx, y: sy, z: 0 });
    Object.assign(shown, target);
    started = false;
  }

  function setPose(p) {
    Object.assign(target, p);
    if (!started) { Object.assign(shown, p); started = true; }
  }

  function setMode(next) {
    mode = next;
    controls.enabled = next === "orbit";
    if (next === "orbit") {
      controls.target.copy(toThree(shown.x, shown.y, shown.z));
      controls.update();
    }
  }

  function setTrailVisible(on) {
    showTrail = on;
    trail.line.visible = on;
    if (!on) trail.reset();
  }

  function clearTrail() {
    trail.reset();
  }

  function resize() {
    const w = container.clientWidth, h = container.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    // exponential chase, framerate-independent
    const k = 1 - Math.exp(-12 * dt);
    shown.x += (target.x - shown.x) * k;
    shown.y += (target.y - shown.y) * k;
    shown.z += (target.z - shown.z) * k;
    // Every angle wraps at 360, so they all chase the short way round. Without
    // this a 350->10 yaw spins almost all the way back the other way, and a
    // back-flip (pitch 0 -> 359 -> 0) tumbles forwards instead of backwards or
    // stalls on its back at 180.
    shown.heading = chaseAngle(shown.heading, target.heading, k);
    shown.pitch = chaseAngle(shown.pitch, target.pitch, k);
    shown.roll = chaseAngle(shown.roll, target.roll, k);

    const pos = toThree(shown.x, shown.y, shown.z);
    drone.group.position.copy(pos);
    drone.group.rotation.set(deg(shown.pitch), -deg(shown.heading), deg(shown.roll), "YXZ");
    drone.setFlying(target.flying, dt);
    drone.shadow.position.set(pos.x, 0.012, pos.z);
    drone.shadow.material.opacity = Math.max(0.03, 0.34 - shown.z * 0.09);

    if (showTrail && target.flying) trail.push(pos);

    updateCamera(camera, controls, mode, pos, arenaW, arenaD, dt);
    // Markers are flat discs and the sensor renderer billboards every one of
    // them, so this view does too — otherwise a marker standing in the middle of
    // a corridor is edge-on and invisible from exactly the angle you approach it.
    for (const face of billboards) {
      face.rotation.y = Math.atan2(camera.position.x - face.parent.position.x,
                                   camera.position.z - face.parent.position.z);
    }
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setScene, setPose, setMode, setTrailVisible, clearTrail, getMode: () => mode };
}

// --- camera rigs ------------------------------------------------------------

function updateCamera(camera, controls, mode, pos, w, d, dt) {
  const k = 1 - Math.exp(-6 * dt);
  if (mode === "follow") {
    camera.up.set(0, 1, 0);
    // Stand-off is capped: in a 10 m corridor a camera the length of the room
    // behind the drone is outside the room and looking at a wall.
    const back = Math.min(Math.max(w, d), 6);
    camera.position.lerp(new THREE.Vector3(pos.x, pos.y + back * 0.45, pos.z + back), k);
    camera.lookAt(pos);
  } else if (mode === "topdown") {
    // Frames the whole arena, not the drone: this is the view a student reads as
    // a map, and one that slides around under the drone is useless for that.
    // Straight down makes the default up vector (0,1,0) parallel to the view, so
    // the roll is undefined and the arena spins during the transition — point up
    // at arena north instead, which is also the orientation of the minimap.
    camera.up.set(0, 0, -1);
    const centre = new THREE.Vector3(w / 2, 0, -d / 2);
    camera.position.lerp(new THREE.Vector3(centre.x, fitHeight(camera, w, d), centre.z), k);
    camera.lookAt(centre);
  } else {
    camera.up.set(0, 1, 0);
    controls.target.lerp(pos, k);
    controls.update();
  }
}

/**
 * Height at which a `w` x `d` arena fits both FOVs.
 *
 * The camera's up vector points at arena north, so the room's depth is framed by
 * the *vertical* FOV and its width by the horizontal one — take whichever needs
 * more height, or a corridor gets cropped at both ends.
 */
function fitHeight(camera, w, d) {
  const halfV = deg(camera.fov) / 2;
  const halfH = Math.atan(Math.tan(halfV) * camera.aspect);
  return Math.max((d / 2) / Math.tan(halfV), (w / 2) / Math.tan(halfH)) * 1.15;
}

// --- arena ------------------------------------------------------------------

function disposeChildren(group) {
  group.traverse((o) => {
    o.geometry?.dispose();
    if (Array.isArray(o.material)) o.material.forEach((m) => m.dispose());
    else o.material?.dispose();
  });
  group.clear();
}

/** Builds the room and returns the marker faces that need billboarding. */
function buildArena(group, desc) {
  const w = desc.width_m ?? desc.size_m;
  const d = desc.depth_m ?? desc.size_m;
  const top = desc.wall_height_m ?? 2.8;

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(w, d),
    new THREE.MeshStandardMaterial({ color: COL.floor, roughness: 0.95 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(w / 2, 0, -d / 2);
  floor.receiveShadow = true;
  group.add(floor);

  const grid = buildGrid(w, d, 0.5);
  grid.position.y = 0.004;
  group.add(grid);

  // Glass box: solid enough to read as a room, transparent enough to fly behind.
  const walls = new THREE.Mesh(
    new THREE.BoxGeometry(w, top, d),
    new THREE.MeshStandardMaterial({
      color: COL.wall, transparent: true, opacity: 0.07,
      side: THREE.BackSide, roughness: 1,
    }),
  );
  walls.position.set(w / 2, top / 2, -d / 2);
  group.add(walls);

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(w, top, d)),
    new THREE.LineBasicMaterial({ color: COL.wall, transparent: true, opacity: 0.45 }),
  );
  edges.position.copy(walls.position);
  group.add(edges);

  if (desc.start) group.add(buildStartPad(desc.start));

  const faces = [];
  for (const m of desc.markers) {
    const marker = buildMarker(m);
    group.add(marker.group);
    faces.push(marker.face);
  }
  return faces;
}

/** GridHelper is square-only, so a rectangular room draws its own lines. */
function buildGrid(w, d, step) {
  const pts = [];
  const nx = Math.max(1, Math.round(w / step));
  const nz = Math.max(1, Math.round(d / step));
  for (let i = 0; i <= nx; i++) {
    const x = (i * w) / nx;
    pts.push(x, 0, 0, x, 0, -d);
  }
  for (let i = 0; i <= nz; i++) {
    const z = -(i * d) / nz;
    pts.push(0, 0, z, w, 0, z);
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  return new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: COL.grid }));
}

/** Where the drone starts every run — the other end of the A-to-B trip. */
function buildStartPad([x, y]) {
  const pad = new THREE.Mesh(
    new THREE.RingGeometry(0.18, 0.26, 24),
    new THREE.MeshBasicMaterial({
      color: COL.trail, transparent: true, opacity: 0.5, side: THREE.DoubleSide,
    }),
  );
  pad.rotation.x = -Math.PI / 2;
  pad.position.copy(toThree(x, y, 0.006));
  return pad;
}

function buildMarker(m) {
  const g = new THREE.Group();
  g.position.copy(toThree(m.x, m.y, 0));
  const color = MARKER_COL[m.kind] ?? 0x94a3b8;
  const isFire = m.kind === "fire" || m.kind === "destination";

  const post = new THREE.Mesh(
    new THREE.CylinderGeometry(0.02, 0.02, m.height_m, 8),
    new THREE.MeshStandardMaterial({ color: COL.post, roughness: 0.8 }),
  );
  post.position.y = m.height_m / 2;
  post.castShadow = true;
  g.add(post);

  const base = new THREE.Mesh(
    new THREE.CylinderGeometry(0.09, 0.11, 0.02, 16),
    new THREE.MeshStandardMaterial({ color: COL.post }),
  );
  base.position.y = 0.01;
  g.add(base);

  const face = new THREE.Mesh(
    markerGeometry(m),
    new THREE.MeshStandardMaterial({
      color, emissive: color, emissiveIntensity: isFire ? 0.55 : 0.2,
      side: THREE.DoubleSide, roughness: 0.6,
    }),
  );
  face.position.y = m.height_m;
  face.castShadow = true;
  // Facing is set per frame by the billboard pass in createStage — a marker in
  // the middle of a corridor has no wall to lean against, and the sensor
  // renderer billboards every marker anyway.
  g.add(face);

  if (isFire) g.add(new THREE.PointLight(color, 1.6, 1.6, 2)
    .translateY(m.height_m));
  return { group: g, face };
}

function markerGeometry(m) {
  const r = m.size_m / 2;
  if (m.kind.endsWith("square")) return new THREE.PlaneGeometry(m.size_m, m.size_m);
  // a 3-segment circle is a triangle, but its first vertex points along +X
  if (m.kind.endsWith("triangle")) {
    return new THREE.CircleGeometry(r * 1.15, 3).rotateZ(Math.PI / 2);
  }
  return new THREE.CircleGeometry(r, 24);
}

// --- drone ------------------------------------------------------------------

function buildDrone(hfovDeg, aspect) {
  const group = new THREE.Group();
  const u = DRONE_SPAN_M;              // everything below is in span-fractions

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(u * 0.55, u * 0.22, u * 0.75),
    new THREE.MeshStandardMaterial({ color: COL.body, roughness: 0.5, metalness: 0.3 }),
  );
  body.castShadow = true;
  group.add(body);

  const props = [];
  const armMat = new THREE.MeshStandardMaterial({ color: COL.arm, roughness: 0.45 });
  const propMat = new THREE.MeshStandardMaterial({
    color: COL.prop, transparent: true, opacity: 0.75, roughness: 0.3,
  });
  for (const [sx, sz] of [[1, 1], [-1, 1], [1, -1], [-1, -1]]) {
    const ax = sx * u * 0.42, az = sz * u * 0.42;
    const arm = new THREE.Mesh(new THREE.BoxGeometry(u * 0.09, u * 0.09, u * 0.62), armMat);
    arm.position.set(ax / 2, 0, az / 2);
    arm.rotation.y = Math.atan2(ax, az);
    arm.castShadow = true;
    group.add(arm);

    const hub = new THREE.Mesh(
      new THREE.CylinderGeometry(u * 0.07, u * 0.07, u * 0.12, 10), armMat);
    hub.position.set(ax, u * 0.08, az);
    group.add(hub);

    const prop = new THREE.Mesh(
      new THREE.CylinderGeometry(u * 0.26, u * 0.26, u * 0.02, 18), propMat);
    prop.position.set(ax, u * 0.15, az);
    prop.userData.dir = sx * sz;       // counter-rotating pairs, like the real thing
    group.add(prop);
    props.push(prop);
  }

  // nose light: the only way to read heading at a glance in top-down view
  const nose = new THREE.Mesh(
    new THREE.SphereGeometry(u * 0.07, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0x4ade80 }),
  );
  nose.position.set(0, 0, -u * 0.42);
  group.add(nose);

  group.add(buildFovCone(hfovDeg, aspect));

  const shadow = new THREE.Mesh(
    new THREE.CircleGeometry(u * 0.6, 24),
    new THREE.MeshBasicMaterial({ color: 0x000000, transparent: true, opacity: 0.3 }),
  );
  shadow.rotation.x = -Math.PI / 2;

  let spin = 0;
  function setFlying(flying, dt) {
    spin += (flying ? PROP_SPIN : 0) * dt;
    for (const p of props) p.rotation.y = spin * p.userData.dir;
  }
  return { group, shadow, setFlying };
}

/** Wireframe of what the onboard camera can see — the link between the two views. */
function buildFovCone(hfovDeg, aspect) {
  const len = 1.2;
  const halfW = len * Math.tan(deg(hfovDeg) / 2);
  const halfH = halfW / aspect;
  const tip = new THREE.Vector3(0, 0, 0);
  const far = [[-1, -1], [1, -1], [1, 1], [-1, 1]].map(
    ([sx, sy]) => new THREE.Vector3(sx * halfW, sy * halfH, -len));
  const pts = [];
  for (const c of far) pts.push(tip.clone(), c.clone());
  for (let i = 0; i < 4; i++) pts.push(far[i].clone(), far[(i + 1) % 4].clone());
  const lines = new THREE.LineSegments(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: COL.fov, transparent: true, opacity: 0.32 }),
  );
  lines.position.z = -DRONE_SPAN_M * 0.38;
  return lines;
}

// --- flight trail -----------------------------------------------------------

function buildTrail() {
  const positions = new Float32Array(TRAIL_MAX * 3);
  const geom = new THREE.BufferGeometry();
  geom.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geom.setDrawRange(0, 0);
  const line = new THREE.Line(geom, new THREE.LineBasicMaterial({
    color: COL.trail, transparent: true, opacity: 0.7,
  }));
  line.frustumCulled = false;
  let count = 0;
  const last = new THREE.Vector3(Infinity, Infinity, Infinity);

  return {
    line,
    push(p) {
      if (p.distanceTo(last) < 0.02) return;
      last.copy(p);
      if (count === TRAIL_MAX) {           // drop the oldest half, keep it cheap
        positions.copyWithin(0, TRAIL_MAX * 3 / 2);
        count = TRAIL_MAX / 2;
      }
      positions.set([p.x, p.y, p.z], count * 3);
      count += 1;
      geom.setDrawRange(0, count);
      geom.attributes.position.needsUpdate = true;
      geom.computeBoundingSphere();
    },
    reset() {
      count = 0;
      last.set(Infinity, Infinity, Infinity);
      geom.setDrawRange(0, 0);
    },
  };
}
