"""The console renders whatever the server emits, so the two vocabularies must
match exactly. They drifted once already: the UI called the first gate "id"
while the engine emitted "identity", so a passing right-party check silently
rendered as Pending — on the one gate whose failure matters most.
"""
import re
from pathlib import Path

from voicebot.compliance.gates import Gates

UI = Path(__file__).resolve().parents[1] / "ui" / "demo-console.html"


def _ui_text() -> str:
    return UI.read_text(encoding="utf-8")


def _ui_gate_ids() -> set[str]:
    """Pull ids from the GATES declaration only.

    Matching any {id, label} pair in the file picks up the language and voice
    selectors too — a false failure that says nothing about the gate contract.
    """
    ui = _ui_text()
    start = ui.index("var GATES")
    block = ui[start:ui.index("];", start)]
    return set(re.findall(r'id:\s*"([a-z]+)"', block))


def test_ui_gate_ids_match_engine_gates():
    ui_ids = _ui_gate_ids()
    engine_gates = set(Gates().as_dict())
    assert ui_ids == engine_gates, (
        f"console gates {sorted(ui_ids)} != engine gates {sorted(engine_gates)}")


def test_ui_handles_every_event_kind_the_server_emits():
    from voicebot import events as ev

    emitted = set()
    for name in dir(ev):
        obj = getattr(ev, name)
        if isinstance(obj, type) and issubclass(obj, ev.Event) and obj is not ev.Event:
            f = obj.__dataclass_fields__.get("kind")
            if f is not None and isinstance(f.default, str):
                emitted.add(f.default)

    from voicebot.events import INTERNAL_KINDS

    ui = _ui_text()
    handled = set(re.findall(r'e\.kind === "([a-z]+)"', ui))
    missing = emitted - handled - INTERNAL_KINDS
    assert not missing, f"console does not handle server events: {sorted(missing)}"


def test_console_uses_no_gate_name_the_engine_does_not_know():
    """A gate the console renders but the engine never sets would sit at
    Pending forever and read as a missing check."""
    assert _ui_gate_ids() <= set(Gates().as_dict())


def test_console_offers_no_control_the_server_cannot_honour():
    """The MLX/CUDA toggle used to be a lie — the backend is fixed by the
    profile the server starts with, and clicking it only changed a label.
    Every remaining control must map to something `start` actually accepts."""
    ui = _ui_text()
    assert 'id="bk-cuda"' not in ui and 'id="bk-mlx"' not in ui, \
        "the fake backend switch is back"

    import re
    sent = set(re.findall(r'type:\s*"start"[^}]*', ui))
    assert sent, "no start message found"
    payload = sent.pop()
    for field in ("policy_id", "register", "voice", "lang"):
        assert field in payload, f"the console never sends {field}"


def test_transcripts_are_not_cleared_between_calls():
    """The right-hand column is the record, not a scratch view — a call that
    scrolls away must still be there."""
    ui = _ui_text()
    assert "callsep" in ui, "calls should be separated, not erased"
    # beginCall must not wipe the transcript node
    start = ui.index("function beginCall")
    body = ui[start:ui.index("\n  }", start)]
    assert 'getElementById("tx").innerHTML = ""' not in body
    assert "addSeparator" in body, "each call should open with a header"


def test_the_whole_console_fits_one_screen():
    """No scrolling: the control column must not need a scrollbar, or the call
    buttons end up below the fold on a 768px-tall laptop."""
    ui = _ui_text()
    assert "height: 100dvh" in ui and "overflow: hidden" in ui, "the shell should be viewport-locked"
    # The control column previously scrolled as a whole; anything that needs a
    # scrollbar there is a sign the layout has grown again.
    assert ".col.controls { overflow-y: auto" not in ui, \
        "the control column is scrolling again — compact it instead"


def test_register_difference_is_shown_not_asserted():
    """An operator who only ever runs one register cannot otherwise tell the
    switch does anything."""
    ui = _ui_text()
    assert 'id="preview"' in ui
    assert "/api/preview" in ui, "the preview must come from the server, not a hardcoded sample"


def test_the_call_control_sits_with_the_waveform():
    """One round control under the wave: start, then mute/unmute — the shape
    every other voice app uses, so the primary action is one press away."""
    ui = _ui_text()
    wave = ui.index('id="wave"')
    orb = ui.index('id="orb"')
    stage_end = ui.index('<!-- ============ RIGHT')
    assert wave < orb < stage_end, "the call control is not in the centre stage"


def test_the_orb_covers_every_state_it_can_be_in():
    """A control that starts a call and then mutes it has four states; missing
    one leaves the button lying about what pressing it will do."""
    import re

    ui = _ui_text()
    body = ui[ui.index("function setOrb"):ui.index("$(\"orb\").addEventListener")]
    for state in ("idle", "muted", "hearing"):
        assert f'"{state}"' in body, f"setOrb does not handle {state}"
    assert "Start call" in body and "tap to mute" in body


def test_the_primary_control_does_not_depend_on_a_webfont():
    """Icons are masked SVG, not glyphs: a font that fails to load must not
    leave the main button blank."""
    ui = _ui_text()
    assert "--ic-play:url(\"data:image/svg+xml" in ui
    assert "--ic-mic:url(\"data:image/svg+xml" in ui


def test_panels_keep_their_natural_height():
    """The growing wave panel squeezed its neighbours, and because .panel
    clips overflow the call bar lost the top of its own text — visible at some
    viewport sizes only, which is why it read as a misalignment."""
    ui = _ui_text()
    block = ui[ui.index("  .panel {"):ui.index("  .panel.grow")]
    assert "flex: 0 0 auto" in block, "a non-grow panel can be squeezed again"


def test_a_refused_microphone_does_not_leave_the_orb_claiming_to_listen():
    """beginCall sets the orb to "on" before permission is asked; every early
    return out of micStart has to put that back, or the button says it is
    listening while nothing is captured."""
    ui = _ui_text()
    body = ui[ui.index("async function micStart"):ui.index("function micStop")]
    assert body.count("setOrb(") == 3, "an early return out of micStart leaves a stale orb state"


def test_barge_in_needs_sustained_speech_not_one_loud_frame():
    """A single loud frame while the agent talks is a cough, a keyboard, or
    our own audio leaking back — acting on it cut the line mid-sentence."""
    ui = _ui_text()
    assert "BARGE_MS" in ui and "BARGE_MARGIN" in ui
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    assert "armedMs > 0 &&" in frame, "silence would open a turn"
    assert "agentSpeaking ? BARGE_MS : 0" in frame, "barge-in has no dwell time"
    assert "agentSpeaking ? BARGE_MARGIN : NOISE_MARGIN" in frame


def test_the_noise_floor_ignores_the_agents_own_voice():
    """Calibrating while the agent speaks measures the speakers, not the room;
    the inflated floor then swallows the caller's next turn."""
    ui = _ui_text()
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    assert "!talking && !agentSpeaking" in frame


def test_the_floor_is_not_learned_from_the_callers_own_voice():
    """A quiet speaker spends the start of every sentence below the arming
    threshold. Averaging those frames into the floor raised the bar the softer
    they spoke — the caller had to say a whole sentence to be heard at all."""
    ui = _ui_text()
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    assert "rms < onThreshold" in frame


def test_the_dwell_is_read_before_it_is_cleared():
    """`armedMs` was zeroed on the line above the one that used it, so the
    260 ms of the caller's first word swallowed by the barge dwell was never
    sent — the reply arrived with its opening syllable missing."""
    ui = _ui_text()
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    arm = frame[frame.index("armedMs > 0 &&"):]
    assert "var armed = armedMs;" in arm
    assert arm.index("var armed = armedMs;") < arm.index("armedMs = 0;")
    assert "RUNUP_MS + armed" in arm


def test_the_client_is_not_stricter_than_the_gate_that_judges_speech():
    """A reply dropped in the browser never reaches the server's noise gate.
    "yes" and "对" are about 250 ms of voiced audio; at 350 ms the caller had
    to speak a longer sentence before anything happened."""
    import re

    from voicebot import audio_gate

    ui = _ui_text()
    client_ms = int(re.search(r"var MIN_VOICED_MS = (\d+)", ui).group(1))
    assert client_ms <= audio_gate.MIN_VOICED_SECONDS * 1000


def test_the_run_up_covers_a_word_onset():
    """A word does not start at full volume. Below this the recogniser is
    handed a clipped first syllable."""
    import re

    ui = _ui_text()
    assert int(re.search(r"var RUNUP_MS = (\d+)", ui).group(1)) >= 250


def test_the_ring_buffer_holds_everything_the_run_up_can_ask_for():
    """`preroll.slice(-need)` cannot return frames the buffer already dropped;
    it would silently send a shorter run-up than the code asks for."""
    import re

    ui = _ui_text()
    def n(name):
        # Declared in a list on one `var` line, or on its own.
        return int(re.search(name + r" = (\d+)", ui).group(1))
    frames = (n("RUNUP_MS") + n("BARGE_MS")) / 21.3 + 1     # ~21 ms a frame
    assert n("PREROLL") >= frames, f"need {frames:.0f} frames of history"


def test_release_does_not_move_with_sensitivity():
    """Release decides when the utterance ended, which is part of every
    reply's latency. Tying it to the arming threshold would make the call feel
    slower every time someone turned sensitivity up."""
    ui = _ui_text()
    assert "var RELEASE_MARGIN" in ui
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    assert "floor * RELEASE_MARGIN" in frame
    assert "NOISE_MARGIN * 0.6" not in frame


def test_sensitivity_can_be_changed_during_a_call():
    """It is the one control you reach for mid-call, when you can hear it is
    not picking you up. The shared `seg` helper freezes while a call runs, so
    this control is built separately."""
    ui = _ui_text()
    start = ui.index("function micSensitivity")
    block = ui[start:ui.index("function micStart", start)]
    assert "if (running) return" not in block
    assert "calibN = 0" in block, "a new threshold needs the room measured again"


def test_only_the_needed_run_up_is_sent_with_a_turn():
    """The ring buffer is sized for the barge-in dwell. Flushing all of it on
    an ordinary turn stapled half a second of silence to the front, which the
    server's own noise gate then read as mostly silence."""
    ui = _ui_text()
    frame = ui[ui.index("function onFrame"):ui.index("async function micStart")]
    assert "preroll.slice(-need)" in frame, "the whole ring buffer is still flushed"


def test_the_sensitivity_control_is_built_after_its_own_constants():
    """`var` hoists the name but not the value. Built at the top of the
    script, SENSITIVITY was still undefined and the whole client stopped
    there — no voice studio, no voice switch, no mic."""
    ui = _ui_text()
    assert ui.index("var SENSITIVITY =") < ui.index("function micSensitivity")


def test_the_recording_script_is_shown_before_recording_starts():
    """"Could not find a steady pitch in that clip" is what you get for
    reading two words. The script has to be on screen before the button is
    pressed, not revealed once it is too late to read it."""
    ui = _ui_text()
    body = ui[ui.index('id="vsBody"'):ui.index('id="vsList"')]
    prompt = body[body.index('class="vsprompt"'):]
    assert "hidden" not in prompt[:prompt.index(">")]
    assert prompt.count("<br>") >= 5, "one sentence is not a script"
    assert body.index('class="vsprompt"') < body.index('id="vsRec"')


def test_the_recording_gauge_measures_against_a_usable_length():
    """The minimum the server accepts and the length that actually clones
    well are different numbers, and only showing the first gets you a clip
    that is accepted and sounds wrong."""
    import re

    ui = _ui_text()
    got = re.search(r"VS_MIN = (\d+), VS_MAX = (\d+), VS_TARGET = (\d+)", ui)
    lo, _, target = (int(g) for g in got.groups())
    assert target > lo
    assert "usable now" in ui and "keep reading" in ui


def test_the_studio_lists_shipped_voices_as_well_as_recorded_ones():
    """Warming a shipped voice used to need the command line."""
    ui = _ui_text()
    block = ui[ui.index("function vsRefresh"):ui.index("$(\"vsToggle\")")]
    assert "d.voices.forEach" in block, "still filtering to custom voices only"
    assert 'if (v.custom) act("Delete"' in block
    assert 'if (slider)' in block, "shipped voices have no pitch slider to bind"


def test_the_voice_picker_shows_more_than_a_name():
    """Six voices do not fit a button strip in a 150px column, and a name
    alone is not enough to choose between them: the accent, the pitch and
    whether it is ready to speak all change the decision."""
    ui = _ui_text()
    block = ui[ui.index("function drawVoices"):ui.index("function setRegister")]
    assert 'className = "vrow"' in block
    assert '"radio"' in block and 'aria-checked' in block
    assert "vdot" in block, "no warm indicator"
    assert "sample.wav" in block, "no way to hear a voice before picking it"


def test_auditioning_a_voice_is_not_choosing_it():
    """The moment you most want to hear the alternative is during a call,
    when you can hear the one you have. Playing must not switch the voice
    mid-call, and must not be blocked by one either."""
    ui = _ui_text()
    block = ui[ui.index("function drawVoices"):ui.index("function paintVoices")]
    play = block[block.index('play.addEventListener'):block.index('row.appendChild')]
    assert "stopPropagation" in play, "playing would also select the row"
    assert "if (running) return" not in play
    row = block[block.index('row.addEventListener'):]
    assert "if (running) return" in row, "voice changed mid-call"


def test_a_voice_with_no_sample_gets_no_play_button():
    ui = _ui_text()
    block = ui[ui.index("function drawVoices"):ui.index("function paintVoices")]
    assert "if (v.sample !== false) row.appendChild(play)" in block


def test_the_wait_for_a_reply_is_shown():
    """A few milliseconds on a warmed voice, several seconds on a cold one.
    An interface that shows nothing through that gap reads as a dropped
    call."""
    ui = _ui_text()
    assert ".orb.thinking" in ui and ".msg.pending" in ui
    block = ui[ui.index("function setWorking"):ui.index("function apply")]
    assert "prefers-reduced-motion" in ui
    # It has to start where the caller's turn ends, both ways in.
    assert 'send({ type: "utterance_end"' in ui
    tail = ui[ui.index('send({ type: "utterance_end"'):]
    assert "setWorking(true)" in tail[:200], "speech turn shows no wait"
    for at in range(len(ui)):
        at = ui.find('send({ type: "say"', at)
        if at < 0:
            break
        assert "setWorking(true)" in ui[at:at + 220], \
            "a turn goes out with no wait shown"
        at += 1


def test_the_wait_indicator_always_has_a_way_out():
    """A ring turning over a call that is not coming back is worse than no
    ring at all."""
    ui = _ui_text()
    block = ui[ui.index("function setWorking"):ui.index("function pendingBubble")]
    assert "setTimeout" in block and "30000" in block
    apply_fn = ui[ui.index("function apply(e)"):ui.index("// ---------------------------------------------------------------- socket")]
    for clears in ('if (e.speaker === "agent") setWorking(false)',
                   'e.kind === "handoff"', 'e.kind === "end"'):
        assert clears in apply_fn, clears


def test_starting_a_call_on_a_cold_voice_is_warned_about_loudly():
    """13.6 seconds on a turn that reads from a warm cache in 5 ms. That
    earns a warning and a way to fix it, not a grey footnote."""
    ui = _ui_text()
    assert ".vpickhint.cold" in ui
    block = ui[ui.index("function updateVoiceHint"):ui.index("function setRegister")]
    assert "synthesised during the call" in block and "milliseconds" in block
    assert "vsWarm(voice)" in block, "no way to fix it from where it is said"


def test_the_controls_column_never_hides_a_control():
    """Its own rule: "compact the column rather than adding a scrollbar — a
    toggle hidden below a fold is a toggle nobody uses." Seven voices broke
    it, so the picker collapses to the one in use and the one panel that is
    reference rather than control takes up the slack."""
    ui = _ui_text()
    assert ".vpick.collapsed .vrow[aria-checked=\"false\"] { display: none; }" in ui
    assert "#policyPanel { flex: 0 1 auto" in ui
    assert "#policyPanel .pbody { overflow-y: auto" in ui
    # And nothing in the column may scroll: compacting comes first, always.
    assert ".col.controls { overflow: hidden; }" in ui


def test_the_register_preview_is_not_fetched_while_hidden():
    ui = _ui_text()
    block = ui[ui.index("function refreshPreview"):]
    assert '$("preview").hidden' in block[:220]


def test_the_cold_warning_is_proportional_to_what_is_missing():
    """"Every line is synthesised during the call" is alarming and, at 228 of
    230 rendered, untrue."""
    ui = _ui_text()
    block = ui[ui.index("function updateVoiceHint"):ui.index("function setRegister")]
    assert "Nearly warm" in block
    assert "r.total * 0.1" in block
