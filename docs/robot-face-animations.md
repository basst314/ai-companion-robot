# Robot Face Animations

This document describes the current browser-backed robot face stack and the behavior vocabulary we are using while tuning the face.

## Big Picture

The robot face is a browser-rendered canvas UI. Python owns the high-level robot state: lifecycle, emotion, sleep timing, overlays, and event dispatch. JavaScript owns the expressive animation: eye shapes, mouth drawing, glow, idle behaviors, one-shot animation clips, border reaction, and smoothing.

Main files:

- `src/ui/browser_service.py`: starts the browser UI and sends websocket commands.
- `src/ui/browser_protocol.py`: maps Python state and events into browser messages.
- `src/ui/face.py`: Python presentation controller for activity, sleep, and state bookkeeping.
- `src/ui/browser_assets/robot-face/runtime-app.js`: websocket client and DOM overlay glue.
- `src/ui/browser_assets/robot-face/engine.js`: canvas animation engine.
- `src/ui/browser_assets/robot-face/state.js`: default visual state and named presets.

## State Flow

The orchestrator calls `ui.render_state(lifecycle, emotion, preview_text)`. The browser service sends a `renderer_state` websocket message:

```json
{
  "type": "renderer_state",
  "payload": {
    "scene": "face",
    "displaySleepRequested": false,
    "lifecycle": "idle|listening|speaking",
    "emotion": "neutral|listening|thinking|speaking|curious",
    "previewText": "..."
  }
}
```

`runtime-app.js` receives this and calls `engine.setExternalState(...)`.

Robot events can also create one-shot animation triggers:

- Wake-word `LISTENING` triggers `attention_mode`.
- Follow-up or playback-complete `LISTENING` does not trigger `attention_mode`.
- Barge-in `LISTENING` events (`playback_barge_in` or `local_barge_in`) trigger `surprise`.
- `SPEAKING` no longer triggers a transient animation; speaking is handled by persistent state and mouth motion.
- `IDLE` updates activity state and does not trigger a one-shot animation.

## Persistent Expressions

In the browser engine, lifecycle and emotion map to preset patches:

- `scene === "sleep"` -> `Sleepy`
- `emotion === "thinking"` -> `Thinking`
- `emotion === "curious"` -> `Curious`
- otherwise -> base neutral/default state

Preset vocabulary in `state.js`:

- `Neutral`: default cyan robot face, slight deadpan.
- `Deadpan`: still, flat mouth, bored lids.
- `Bored`: lower-energy, tiny dash mouth, long pauses.
- `Curious`: alert/upward look, asymmetry, faster idle.
- `Cute`: bigger eyes, closer spacing, curved mouth.
- `Thinking`: upward/asymmetric eyes, flat mouth, focused/puzzled.
- `Mischievous`: sly tilt, brighter glow, small curve mouth.
- `Sleepy`: closed/resting eyes with a breathing wave mouth.
- `Alert`: bigger/brighter eyes, ready expression.
- `Funny tearful`: sparkle/reflection-heavy exaggerated look.
- `Oreo vibe`: balanced alternate style, available but not automatically selected.

## Transient Animations

These one-shot clips are available in `engine.js` and can be fired from the interactive console by typing their number and pressing Enter. Type `i` and press Enter to toggle normal idle animations while testing.

1. `blink`: closes and opens lids; also nudges mouth slightly.
2. `quick_glance`: snap left/right, cross to the other side, then return center.
3. `bored`: drops lids, slight side/down look, negative mouth curve.
4. `cute`: larger/cuter eyes, smile, mild sparkle intensity.
5. `thinking`: side-to-side/upward thought motion with asymmetric lids and slight frown.
6. `attention_mode`: eyes open, boredness suppressed, mouth still.
7. `surprise`: wide/open eyes, stretch, upward bounce, open mouth, sparkle intensity.
8. `deadpan`: heavy lid stare, still mouth, negative smile.
9. `sleeping`: closes eyes, sleep-line signal, breathing wave mouth.
10. `scoot`: tiny lateral swoosh/squish.

Other implemented clips such as `double_blink`, directional looks, `curious`, and `boundary_press` remain in code for later reuse, but they are not in the primary debug shortcut set and are not used automatically by idle.

The browser display has a separate animation badge next to the lifecycle badge. It shows the currently active clip label while a transient or idle animation is running. The interactive console keeps the shortcut mapping in the sticky header so it remains visible while logs scroll.

## Idle Animations

Idle is handled mostly in JavaScript. It runs only when:

- idle policy is enabled,
- `scene === "face"`,
- `lifecycle === "idle"`,
- there are no active clips,
- at least about `2.2s` have passed since recent interaction,
- the next scheduled idle time has arrived.

Default allowed idle behaviors are:

```text
blink, quick_glance, bored, cute, thinking, scoot
```

Policy notes:

- `quick_glance` has a lower weight than before, so it appears less often.
- `bored` is only eligible after more than `20s` since the last external interaction. Once it starts, it holds for roughly `5-8s`, blocking other idle clips.
- `cute` idle holds for roughly `5-8s`; its sine-wave mouth follows a shallow upward smile baseline.
- `thinking` idle holds each side for roughly `2-4s` before switching.
- `scoot` is occasional and stretched to roughly `2-3s`.
- `double_blink`, directional looks, `curious`, `surprise`, `deadpan`, `sleep`, and `boundary_press` are not used as default idle clips.

Idle timing is controlled by:

- `AI_COMPANION_UI_FACE_IDLE_ENABLED`
- `AI_COMPANION_UI_FACE_IDLE_FREQUENCY`
- `AI_COMPANION_UI_FACE_IDLE_INTENSITY`
- `AI_COMPANION_UI_FACE_IDLE_PAUSE_RANDOMNESS`
- `AI_COMPANION_UI_FACE_SECONDARY_MICRO_MOTION`
- `AI_COMPANION_UI_FACE_IDLE_BEHAVIORS`

## Secondary Micro-Motion

The engine applies tiny drifting motion and look targets between larger behaviors. This has been made slightly more visible so the face does not feel frozen during quiet moments.

Sleep mode also gets a slow breathing bob so the closed eyes subtly move up and down.

## Sleep Animation

Python tracks last activity. If lifecycle stays idle longer than `idle_sleep_seconds`, scene becomes `sleep`. After an additional `sleeping_eyes_grace_seconds`, it sets `displaySleepRequested`.

Browser response:

- `scene: "sleep"` applies `Sleepy`.
- Eyes close and use sleep-line accents.
- The mouth switches to a breathing wave rather than a sad downward curve.
- The `z z Z` accents rise continuously and fade instead of jumping back to the start position.
- Occasional background star sparkles glow, rotate, fade out, then reappear elsewhere.
- Up to 2-3 background sparkles may overlap.
- If `displaySleepRequested` is true, `runtime-app.js` adds `display-blanked`, hides overlays, and Python may run the configured display sleep command.

Any activity wakes it back to face mode.

## Mouth And Speech

There is no phoneme/viseme system today. Speaking is intentionally tied to speaking mode rather than waveform or mic level.

Speaking visuals:

- lifecycle `speaking` activates mouth motion,
- normal speaking keeps the eyes fully open and centered on the user,
- the mouth uses the existing sine-wave animation from the base face state,
- no transient `scoot` animation is fired when speech starts.

Mic levels still affect the outer border while listening or otherwise active; they do not drive the mouth.

## Realtime AI Animation Tool

Realtime exposes a local `set_face_animation` tool. It is intentionally small and timed, so the model can add meaning-bearing expression moments without taking over idle behavior.

Arguments:

- `animation`: `curious | cute | thinking | deadpan | sleeping | speaking`
- `duration_seconds`: optional, clamped to `0.5-10s`

Defaults:

- `curious`: `4s`
- `cute`: `6.5s`
- `thinking`: `6s`
- `deadpan`: `5s`
- `sleeping`: `6s`
- `speaking`: clears the override immediately and returns to the normal lifecycle face

Tool guidance:

- `curious`: observing, neutral_questioning, processing new information.
- `cute`: cute, thankful, adorable_attention.
- `thinking`: tired, low energy, bored, unimpressed.
- `deadpan`: deadpan stare, dry joke, obvious, flat, understated reaction.
- `sleeping`: eyes closed, sleeping.
- `speaking`: return to normal speaking face.

`camera_snapshot` automatically applies a short `curious` override before capture.

The interactive console logs AI-requested face animations with the selected animation and duration.
