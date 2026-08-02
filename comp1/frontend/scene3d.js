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
  victim: 0xdc2626, post: 0x475569, trail: 0x38bdf8, fov: 0x38bdf8,
};
const MARKER_COL = {
  victim: COL.victim, red_square: COL.victim, blue_circle: 0x2563eb,
  green_triangle: 0x16a34a, yellow_square: 0xeab308,
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
  let arenaSize = 4;
  const clock = new THREE.Clock();

  function setScene(desc) {
    disposeChildren(arena);          // a reconnect rebuilds the arena from scratch
    trail.reset();
    if (!desc) { drone.group.visible = drone.shadow.visible = false; return; }
    arenaSize = desc.size_m;
    buildArena(arena, desc);
    // fog only for the void beyond the arena — pulled in any closer it greys out
    // the room itself, worst of all from the top-down camera
    scene.fog.near = desc.size_m * 2.5;
    scene.fog.far = desc.size_m * 9;
    drone.group.visible = drone.shadow.visible = true;
    Object.assign(target, { x: desc.size_m / 2, y: desc.size_m / 2, z: 0 });
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
    shown.pitch += (target.pitch - shown.pitch) * k;
    // Heading and roll wrap at 360, so they chase the short way round. Without
    // this a 350->10 yaw spins almost all the way back the other way, and a
    // back-flip (roll 0 -> 359 -> 0) tumbles forwards instead of backwards.
    shown.heading = chaseAngle(shown.heading, target.heading, k);
    shown.roll = chaseAngle(shown.roll, target.roll, k);

    const pos = toThree(shown.x, shown.y, shown.z);
    drone.group.position.copy(pos);
    drone.group.rotation.set(deg(shown.pitch), -deg(shown.heading), deg(shown.roll), "YXZ");
    drone.setFlying(target.flying, dt);
    drone.shadow.position.set(pos.x, 0.012, pos.z);
    drone.shadow.material.opacity = Math.max(0.03, 0.34 - shown.z * 0.09);

    if (showTrail && target.flying) trail.push(pos);

    updateCamera(camera, controls, mode, pos, arenaSize, dt);
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return { setScene, setPose, setMode, setTrailVisible, clearTrail, getMode: () => mode };
}

// --- camera rigs ------------------------------------------------------------

function updateCamera(camera, controls, mode, pos, size, dt) {
  const k = 1 - Math.exp(-6 * dt);
  if (mode === "follow") {
    camera.up.set(0, 1, 0);
    camera.position.lerp(new THREE.Vector3(pos.x, pos.y + size * 0.45, pos.z + size), k);
    camera.lookAt(pos);
  } else if (mode === "topdown") {
    // Frames the whole arena, not the drone: this is the view a student reads as
    // a map, and one that slides around under the drone is useless for that.
    // Straight down makes the default up vector (0,1,0) parallel to the view, so
    // the roll is undefined and the arena spins during the transition — point up
    // at arena north instead, which is also the orientation of the minimap.
    camera.up.set(0, 0, -1);
    const centre = new THREE.Vector3(size / 2, 0, -size / 2);
    camera.position.lerp(new THREE.Vector3(centre.x, fitHeight(camera, size), centre.z), k);
    camera.lookAt(centre);
  } else {
    camera.up.set(0, 1, 0);
    controls.target.lerp(pos, k);
    controls.update();
  }
}

/** Height at which a `size` x `size` arena fits the narrower of the two FOVs. */
function fitHeight(camera, size) {
  const halfV = deg(camera.fov) / 2;
  const halfH = Math.atan(Math.tan(halfV) * camera.aspect);
  return (size / 2) * 1.15 / Math.tan(Math.min(halfV, halfH));
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

function buildArena(group, desc) {
  const s = desc.size_m, top = desc.wall_height_m ?? 2.8;

  const floor = new THREE.Mesh(
    new THREE.PlaneGeometry(s, s),
    new THREE.MeshStandardMaterial({ color: COL.floor, roughness: 0.95 }),
  );
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(s / 2, 0, -s / 2);
  floor.receiveShadow = true;
  group.add(floor);

  const grid = new THREE.GridHelper(s, Math.round(s / 0.5), COL.gridMajor, COL.grid);
  grid.position.set(s / 2, 0.004, -s / 2);
  group.add(grid);

  // Glass box: solid enough to read as a room, transparent enough to fly behind.
  const walls = new THREE.Mesh(
    new THREE.BoxGeometry(s, top, s),
    new THREE.MeshStandardMaterial({
      color: COL.wall, transparent: true, opacity: 0.07,
      side: THREE.BackSide, roughness: 1,
    }),
  );
  walls.position.set(s / 2, top / 2, -s / 2);
  group.add(walls);

  const edges = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(s, top, s)),
    new THREE.LineBasicMaterial({ color: COL.wall, transparent: true, opacity: 0.45 }),
  );
  edges.position.copy(walls.position);
  group.add(edges);

  for (const m of desc.markers) group.add(buildMarker(m, s));
}

function buildMarker(m, size) {
  const g = new THREE.Group();
  g.position.copy(toThree(m.x, m.y, 0));
  const color = MARKER_COL[m.kind] ?? 0x94a3b8;
  const isVictim = m.kind === "victim";

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
      color, emissive: color, emissiveIntensity: isVictim ? 0.55 : 0.2,
      side: THREE.DoubleSide, roughness: 0.6,
    }),
  );
  face.position.y = m.height_m;
  face.castShadow = true;
  // Markers stand against an arena wall, so they face the middle of the room.
  // A plane's normal is +Z, which a rotation of t about Y sends to
  // (sin t, 0, cos t); three.z = -y, so pointing it at the centre needs
  // t = atan2(dx, -dy). Get this wrong and every marker is drawn edge-on.
  const dx = size / 2 - m.x, dy = size / 2 - m.y;
  face.rotation.y = Math.atan2(dx, -dy);
  g.add(face);

  if (isVictim) g.add(new THREE.PointLight(color, 1.6, 1.6, 2)
    .translateY(m.height_m));
  return g;
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
