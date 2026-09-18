// Automatic buffering of the block workspace, so a session survives the page.
//
// Until this file existed the workspace was rebuilt from nothing on every load:
// a closed tab, a reloaded page, a laptop that slept through lunch, or the
// program being closed and reopened between heats all threw away whatever was
// on screen. Students do not think of a browser tab as a document they have to
// save, and nothing in the app told them otherwise, so the work simply went.
//
// Two rules hold the design together. Both are borrowed from comp1/settings.py,
// which solves the same problem on the Python side.
//
// **Nothing here may throw.** A buffer is a convenience, never a dependency.
// A full storage quota, a private-browsing window where touching localStorage
// raises, a half-written entry, a saved workspace naming a block this version
// no longer has — every one of those has to end as "no buffer today", leaving a
// student with an empty but working workspace. Losing yesterday's blocks is a
// disappointment; a page that will not load is the end of the session.
//
// **The workspace on screen is the truth.** What is stored here is a copy taken
// after the fact, so a buffer that cannot be understood is discarded rather
// than repaired, and it is never merged into a workspace that already has
// blocks in it. Restoring happens once, before the student can touch anything.

// Versioned in the key as well as the envelope: the key so a future format can
// be introduced without racing an older tab still writing the old one, the
// envelope so this version can recognise a buffer left by a newer one.
const BUFFER_KEY = "comp1.blockBuffer.v1";
const BUFFER_FORMAT = 1;

// An origin gets roughly 5 MB of localStorage. A workspace large enough to pass
// this is one no student built by dragging, so writing it is far more likely to
// be a bug than a rescue — and a setItem that throws on every edit is worse
// than no buffer at all.
const MAX_CHARS = 512 * 1024;

// A drag emits a steady stream of change events and setItem is synchronous, so
// writes are coalesced. This is also the worst case for how much work a power
// cut can cost, which is why it is a second rather than the ten that would be
// even cheaper.
const SAVE_DELAY_MS = 1000;

function drop(storage) {
  try {
    storage.removeItem(BUFFER_KEY);
  } catch (_error) {
    // Nothing left to try. The next write overwrites it anyway.
  }
}

/**
 * Read the buffered workspace state.
 *
 * Returns `{state, dropped}`: `state` is the Blockly serialization to load, or
 * null if there is nothing usable, and `dropped` says whether something was
 * there and had to be thrown away — the difference between a first run and a
 * lost session, which is the difference between saying nothing to the student
 * and owing them an explanation.
 */
function readState(storage) {
  let raw;
  try {
    raw = storage.getItem(BUFFER_KEY);
  } catch (_error) {
    return { state: null, dropped: false };
  }
  if (!raw) return { state: null, dropped: false };

  let envelope;
  try {
    envelope = JSON.parse(raw);
  } catch (_error) {
    // Unparseable now is unparseable forever, so it goes rather than being
    // re-read and re-rejected on every load for the rest of the machine's life.
    drop(storage);
    return { state: null, dropped: true };
  }
  if (!envelope || typeof envelope !== "object") {
    drop(storage);
    return { state: null, dropped: true };
  }
  // A format change gets a new number here rather than a migration. One lost
  // session's blocks is a cheaper failure than a workspace half-translated into
  // a shape nobody can explain to the student looking at it.
  if (envelope.format !== BUFFER_FORMAT || !envelope.workspace
      || typeof envelope.workspace !== "object") {
    drop(storage);
    return { state: null, dropped: true };
  }
  return { state: envelope.workspace, dropped: false };
}

/** Write the workspace state. False means it was not stored, never an error. */
function writeState(storage, state) {
  if (!state || typeof state !== "object") return false;
  let raw;
  try {
    // `saved` is never read back by this code. It is here for the human being
    // asked why a venue machine is showing last week's program.
    raw = JSON.stringify({
      format: BUFFER_FORMAT,
      saved: new Date().toISOString(),
      workspace: state,
    });
  } catch (_error) {
    return false;
  }
  if (raw.length > MAX_CHARS) return false;
  try {
    storage.setItem(BUFFER_KEY, raw);
    return true;
  } catch (_error) {
    // Out of quota, or storage denied outright. The previous entry is left
    // alone deliberately: a slightly stale buffer beats none.
    return false;
  }
}

/**
 * Build a buffer over a storage and a Blockly.
 *
 * Both are injected rather than reached for, so the tests can drive this with a
 * plain object for storage and a real headless Blockly — and so a browser that
 * denies storage, or a Blockly built without the serialization API, degrades to
 * a buffer whose methods all politely do nothing.
 */
function createBuffer(options) {
  const { storage = null, blockly = null } = options || {};
  const workspaces = blockly && blockly.serialization && blockly.serialization.workspaces;
  const available = Boolean(
    storage && workspaces
    && typeof workspaces.save === "function"
    && typeof workspaces.load === "function",
  );

  return {
    available,

    /** Copy the workspace into the buffer. True if it was stored. */
    capture(workspace) {
      if (!available) return false;
      let state;
      try {
        state = workspaces.save(workspace);
      } catch (_error) {
        return false;
      }
      return writeState(storage, state);
    },

    /**
     * Put a buffered workspace back, if there is one.
     *
     * Returns "restored", "none" (nothing was buffered) or "unusable"
     * (something was, and could not be opened). The caller decides what to tell
     * the student; this only decides what is on the screen.
     */
    restore(workspace) {
      if (!available) return "none";
      const { state, dropped } = readState(storage);
      if (!state) return dropped ? "unusable" : "none";
      try {
        // recordUndo is off: the restore is where this session starts, not an
        // edit within it. Ctrl+Z on a freshly opened page must not undo the
        // workspace a student has just been handed back.
        workspaces.load(state, workspace, { recordUndo: false });
      } catch (_error) {
        // Blockly throws part-way through on a block type it does not know —
        // a buffer written by a newer Drone Coder, say — and leaves behind
        // however much of the workspace it had managed to build. That is not a
        // program anyone asked for, so it is cleared rather than shown.
        try {
          workspace.clear();
        } catch (_ignored) {
          // Nothing more to do; the caller seeds a usable workspace regardless.
        }
        drop(storage);
        return "unusable";
      }
      return "restored";
    },

    /** Forget the buffer. The next capture starts it again. */
    clear() {
      if (storage) drop(storage);
    },
  };
}

function browserStorage() {
  try {
    // The property access itself throws when site data is blocked, so this is
    // not the same as checking that localStorage is defined.
    return window.localStorage || null;
  } catch (_error) {
    return null;
  }
}

if (typeof window !== "undefined") {
  window.COMP1_BUFFER = createBuffer({
    storage: browserStorage(),
    blockly: window.Blockly,
  });
  window.COMP1_BUFFER.SAVE_DELAY_MS = SAVE_DELAY_MS;
}
if (typeof module !== "undefined") {
  module.exports = {
    createBuffer, readState, writeState,
    BUFFER_KEY, BUFFER_FORMAT, MAX_CHARS, SAVE_DELAY_MS,
  };
}
