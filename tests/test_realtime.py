"""Actual socket-loop races, using event barriers rather than model timing."""
import asyncio
import json
import struct

import pytest

from voicebot import config, server
from voicebot.call.engine import CallSession
from voicebot.data import personas
from voicebot.events import AgentAudio
from voicebot.recording import Recorder
from voicebot.realtime import RealtimeTransport
from voicebot.runtime.base import Completion, Speech, TranscriptResult
from voicebot.runtime.mock import MockBackend


class Socket:
    def __init__(self):
        self.input = asyncio.Queue()
        self.output = asyncio.Queue()
        self.seen = []
        self.closed = False

    async def accept(self):
        pass

    async def receive(self):
        return await self.input.get()

    async def send_text(self, text):
        obj = json.loads(text)
        self.seen.append(obj)
        await self.output.put(obj)

    async def send_bytes(self, data):
        self.seen.append(data)
        await self.output.put(data)

    async def close(self):
        self.closed = True

    def put(self, **data):
        self.input.put_nowait({'type': 'websocket.receive', 'text': json.dumps(data)})

    async def until(self, kind, **fields):
        async def read():
            while True:
                item = await self.output.get()
                if isinstance(item, dict) and item.get('kind') == kind and all(
                        item.get(k) == v for k, v in fields.items()):
                    return item
        return await asyncio.wait_for(read(), 2)


class Backend(MockBackend):
    def __init__(self):
        super().__init__((0, 0))
        self.block = None
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()
        self.asr_calls = 0

    async def hold(self, phase):
        if self.block == phase:
            self.entered.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    async def speak(self, text, lang, prerendered, voice=None):
        await self.hold('tts')
        return Speech(b'\x01\x00' * 640, 16000, 0)

    async def transcribe(self, pcm, sample_rate):
        self.asr_calls += 1
        await self.hold('asr')
        return TranscriptResult('yes speaking', 'en', 0)

    async def complete(self, *args, **kwargs):
        await self.hold('router')
        return Completion('unclear', 0)


@pytest.fixture
def setup(monkeypatch):
    backend = Backend()
    monkeypatch.setattr(server, '_state', {'cfg': config.load('mock')})
    monkeypatch.setattr(server, '_backend', lambda: backend)
    monkeypatch.setattr(server, 'RECORDER', Recorder())
    monkeypatch.setattr(server, 'is_speech', lambda *args: (True, ''))
    monkeypatch.setattr(server, 'is_plausible', lambda *args: (True, ''))
    monkeypatch.setattr(server, 'trim_silence', lambda pcm, **kw: pcm)
    return backend


async def start(sock):
    sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
    ev = await sock.until('audio_end', client_turn=1)
    sock.put(type='playback_done', generation=ev['generation'], audio_id=ev['audio_id'])
    return ev


async def disconnect(sock, task):
    sock.input.put_nowait({'type': 'websocket.disconnect'})
    await asyncio.wait_for(task, 2)
    assert server._state.get('live_calls', 0) == 0


@pytest.mark.parametrize('phase', ['asr', 'tts', 'router'])
def test_barge_in_cancels_inference_and_accepts_next_turn(setup, phase):
    async def run():
        b, sock = setup, Socket()
        task = asyncio.create_task(server.ws(sock))
        await start(sock)
        offset = 0
        if phase == 'router':
            sock.put(type='say', text='yes speaking', client_turn=2 + offset)
            ev = await sock.until('audio_end', client_turn=2 + offset)
            sock.put(type='playback_done', generation=ev['generation'], audio_id=ev['audio_id'])
            offset = 1
        b.block = phase
        if phase == 'asr':
            sock.input.put_nowait({'bytes': b'\x01\x00' * 1000})
            sock.put(type='utterance_end', client_turn=2 + offset)
        else:
            sock.put(type='say', text='purple flying teapot' if phase == 'router'
                     else 'yes speaking', client_turn=2 + offset)
        await asyncio.wait_for(b.entered.wait(), 2)
        sock.put(type='barge_in', client_turn=3 + offset)
        cancel = await sock.until('audio_cancel', client_turn=3 + offset)
        await asyncio.wait_for(b.cancelled.wait(), 2)
        b.block = None
        sock.put(type='say', text='who is calling?', client_turn=4 + offset)
        end = await sock.until('audio_end', client_turn=4 + offset)
        assert end['generation'] > cancel['generation']
        # Any frame sent after cancellation belongs to the new generation.
        i = sock.seen.index(cancel)
        assert all(struct.unpack_from('<I', x)[0] > cancel['generation']
                   for x in sock.seen[i + 1:] if isinstance(x, bytes))
        await disconnect(sock, task)
    asyncio.run(run())


def test_interrupt_before_first_audio_and_hangup_release_call(setup):
    async def run():
        b, sock = setup, Socket()
        b.block = 'tts'
        task = asyncio.create_task(server.ws(sock))
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        await asyncio.wait_for(b.entered.wait(), 2)
        sock.put(type='hangup', client_turn=2)
        await sock.until('audio_cancel', client_turn=2)
        await asyncio.wait_for(b.cancelled.wait(), 2)
        await sock.until('status', text='Call cancelled')
        assert server._state['live_calls'] == 0
        assert not any(isinstance(x, bytes) for x in sock.seen)
        await disconnect(sock, task)
    asyncio.run(run())


def test_stale_playback_ack_does_not_advance_unheard_prompt(setup):
    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        old = await sock.until('audio_end', client_turn=1)
        sock.put(type='barge_in', client_turn=2)
        await sock.until('audio_cancel', client_turn=2)
        sock.put(type='playback_done', generation=old['generation'], audio_id=old['audio_id'])
        sock.put(type='say', text='yes', client_turn=3)
        await sock.until('audio_end', client_turn=3)
        agent = [x['text'] for x in sock.seen if isinstance(x, dict)
                 and x.get('kind') == 'transcript' and x.get('speaker') == 'agent'
                 and x.get('client_turn') == 3]
        assert agent and 'speaking with' in agent[-1].lower()
        assert not any(x.get('gate') == 'identity' and x.get('state') == 'pass'
                       for x in sock.seen if isinstance(x, dict))
        await disconnect(sock, task)
    asyncio.run(run())


def test_disconnect_cancels_router_child_not_only_response(setup):
    async def run():
        b, sock = setup, Socket()
        task = asyncio.create_task(server.ws(sock))
        await start(sock)
        sock.put(type='say', text='yes speaking', client_turn=2)
        ev = await sock.until('audio_end', client_turn=2)
        sock.put(type='playback_done', generation=ev['generation'], audio_id=ev['audio_id'])
        b.block = 'router'
        sock.put(type='say', text='purple flying teapot', client_turn=3)
        await asyncio.wait_for(b.entered.wait(), 2)
        # Router is running while the thinking audio awaits playback.
        await sock.until('audio_end', client_turn=3)
        await disconnect(sock, task)
        assert b.cancelled.is_set()
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()
                    and not t.done()]
    asyncio.run(run())


def test_oversized_audio_is_discarded_and_next_turn_recovers(setup):
    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        await start(sock)
        sock.input.put_nowait({'bytes': b'\0' * (16000 * 2 * 30 + 2)})
        sock.put(type='utterance_end', client_turn=2)
        await sock.until('status', text='Please keep each reply under 30 seconds.')
        assert setup.asr_calls == 0
        sock.input.put_nowait({'bytes': b'\x01\0' * 1000})
        sock.put(type='utterance_end', client_turn=3)
        await sock.until('audio_end', client_turn=3)
        assert setup.asr_calls == 1
        await disconnect(sock, task)
    asyncio.run(run())


def test_single_state_owner_even_when_backend_delays_cancellation():
    async def run():
        entered, cancelled, release, second = [asyncio.Event() for _ in range(4)]
        sent = []
        async def send(x):
            sent.append(x)
        t = RealtimeTransport(send)
        async def old(g):
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
                await t.send({'old': True}, g)
        async def new(g):
            second.set()
        t.submit(old, lambda: None)
        await entered.wait()
        t.submit(new, lambda: None)
        await cancelled.wait()
        assert not second.is_set()
        release.set()
        await asyncio.wait_for(second.wait(), 2)
        assert not any(x.get('old') for x in sent)
        await t.close()
    asyncio.run(run())


def test_bounded_writer_drops_queued_stale_frames():
    async def run():
        entered, release, new_sent = [asyncio.Event() for _ in range(3)]
        sent = []
        async def send(x):
            if not entered.is_set():
                entered.set()
                await release.wait()
            sent.append(x)
            if x == {'new': True}:
                new_sent.set()
        t = RealtimeTransport(send, queue_size=2)
        async def old(g):
            for n in range(100):
                await t.send({'old': n}, g)
        async def new(g):
            await t.send({'new': True}, g)
        t.submit(old, lambda: None)
        await entered.wait()
        t.submit(new, lambda: None)
        release.set()
        await asyncio.wait_for(new_sent.wait(), 2)
        assert not any('old' in x for x in sent)
        await t.close()
    asyncio.run(run())


def test_interrupted_consent_does_not_become_permission():
    async def run():
        s = CallSession(personas.get('TH-4471-0093'), Backend())
        s.turn = 6
        s._pending = 'cross_sell'
        s.interrupt()
        events = [e async for e in s.on_caller('yes')]
        assert not s._cross_sell_ok
        assert s._pending == 'cross_sell'
        assert any(isinstance(e, AgentAudio) for e in events)
        assert not any(e.kind == 'tool' for e in events)
    asyncio.run(run())


def test_interrupt_preserves_verified_identity_and_dnc_priority():
    async def run():
        s = CallSession(personas.get('TH-4471-0093'), Backend())
        _ = [e async for e in s.start()]
        _ = [e async for e in s.on_caller('yes speaking')]
        before = s.gates.as_dict()['identity']
        s.interrupt()
        events = [e async for e in s.on_caller('stop calling me')]
        assert before == s.gates.as_dict()['identity'] == 'pass'
        assert s.ended
        assert any(e.kind == 'tool' for e in events)
    asyncio.run(run())


def test_normal_yes_after_playback_ack_still_verifies_identity(setup):
    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        await start(sock)
        # Queue immediately after the acknowledgement to cover the wakeup race.
        sock.put(type='say', text='yes', client_turn=2)
        await sock.until('audio_end', client_turn=2)
        assert any(x.get('gate') == 'identity' and x.get('state') == 'pass'
                   for x in sock.seen if isinstance(x, dict))
        await disconnect(sock, task)
    asyncio.run(run())


def test_response_does_not_finish_until_browser_acknowledges(setup):
    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        end = await sock.until('audio_end', client_turn=1)
        assert not any(isinstance(x, dict) and x.get('kind') == 'response_done'
                       for x in sock.seen)
        sock.put(type='playback_done', generation=end['generation'], audio_id=end['audio_id'])
        await sock.until('response_done', client_turn=1)
        await disconnect(sock, task)
    asyncio.run(run())


def test_real_asgi_websocket_negotiates_audio_protocol(setup):
    from fastapi.testclient import TestClient
    with TestClient(server.app).websocket_connect('/ws') as sock:
        sock.send_json({'type':'start', 'policy_id':'TH-4471-0093',
                        'audio_protocol':2, 'client_turn':1})
        frame_count = 0
        while True:
            msg = sock.receive()
            if msg.get('bytes') is not None:
                gen, aid, seq = struct.unpack_from('<III', msg['bytes'])
                assert seq == frame_count
                frame_count += 1
            else:
                ev = json.loads(msg['text'])
                if ev['kind'] == 'audio_end':
                    break
        assert frame_count > 0
        assert (gen, aid) == (ev['generation'], ev['audio_id'])
        sock.send_json({'type':'playback_done', 'generation':gen, 'audio_id':aid})
        while sock.receive_json()['kind'] != 'response_done':
            pass
        sock.send_json({'type':'hangup', 'client_turn':2})
        while sock.receive_json()['kind'] != 'status':
            pass
    assert server._state['live_calls'] == 0


def test_input_queued_with_start_cannot_bypass_identity(setup):
    async def run():
        sock = Socket()
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        sock.put(type='say', text='yes', client_turn=2)
        task = asyncio.create_task(server.ws(sock))
        await sock.until('audio_end', client_turn=2)
        assert not any(x.get('gate') == 'identity' and x.get('state') == 'pass'
                       for x in sock.seen if isinstance(x, dict))
        said = [x['text'] for x in sock.seen if isinstance(x, dict)
                and x.get('speaker') == 'agent' and x.get('client_turn') == 2]
        assert said and 'speaking with' in said[-1].lower()
        await disconnect(sock, task)
    asyncio.run(run())


def test_playback_measurement_is_recorded_once_with_server_owned_role(setup):
    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        await start(sock)
        sock.input.put_nowait({'bytes': b'\x01\0' * 1000})
        sock.put(type='utterance_end', client_turn=2, endpoint_ms=710)
        end = await sock.until('audio_end', client_turn=2)
        for value in (1500, 1):
            sock.put(type='playback_started', generation=end['generation'],
                     audio_id=end['audio_id'], first_audio_ms=value,
                     method='media_playing_event', role='acknowledgement')
        played = await sock.until('playback_started', client_turn=2)
        assert played['role'] == 'answer'
        assert played['first_answer']
        sock.put(type='playback_done', generation=end['generation'], audio_id=end['audio_id'])
        await sock.until('response_done', client_turn=2)
        call = server.RECORDER.get(server.RECORDER.summaries()[0]['id'])
        rows = [e for e in call.events if e['kind'] == 'response_metrics' and e['client_turn'] == 2]
        assert len(rows) == 1
        assert rows[0]['audio'][0]['client_first_audio_ms'] == 1500
        assert rows[0]['endpoint_ms'] == 710
        assert rows[0]['profile'] == 'mock'
        assert {op['stage'] for op in rows[0]['operations']} == {'asr','tts'}
        assert rows[0]['operations'][0]['worker_queue_ms'] is None  # mock has no worker
        await disconnect(sock, task)
    asyncio.run(run())


def test_interrupted_trace_is_flushed_before_hangup(setup, monkeypatch, tmp_path):
    recorder = Recorder(tmp_path / 'calls.jsonl')
    monkeypatch.setattr(server, 'RECORDER', recorder)
    async def run():
        sock = Socket()
        setup.block = 'tts'
        task = asyncio.create_task(server.ws(sock))
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        await asyncio.wait_for(setup.entered.wait(), 2)
        sock.put(type='hangup', client_turn=2)
        await sock.until('status', text='Call cancelled')
        await disconnect(sock, task)
    asyncio.run(run())
    call = json.loads((tmp_path / 'calls.jsonl').read_text().splitlines()[0])
    traces = [e for e in call['events'] if e['kind'] == 'response_metrics']
    assert len(traces) == 1 and traces[0]['status'] == 'interrupted'
    assert not traces[0]['audio']


def test_overload_keeps_socket_usable_without_confirming_unheard_question(setup, monkeypatch):
    from voicebot.runtime.workers import InferenceBusy
    original = setup.speak
    busy = [True]
    async def speak(*args, **kwargs):
        if busy[0]:
            raise InferenceBusy('full')
        return await original(*args, **kwargs)
    monkeypatch.setattr(setup, 'speak', speak)

    async def run():
        sock = Socket()
        task = asyncio.create_task(server.ws(sock))
        sock.put(type='start', policy_id='TH-4471-0093', audio_protocol=2, client_turn=1)
        status = await sock.until('status', client_turn=1,
                                  text='Voice service is busy. Please try again shortly.')
        assert 'busy' in status['text']
        await sock.until('response_done', client_turn=1)
        call = server.RECORDER.get(server.RECORDER.summaries()[0]['id'])
        assert any(e.get('status') == 'overloaded' for e in call.events)
        busy[0] = False
        sock.put(type='say', text='yes', client_turn=2)
        await sock.until('audio_end', client_turn=2)
        assert not any(x.get('gate') == 'identity' and x.get('state') == 'pass'
                       for x in sock.seen if isinstance(x, dict))
        await disconnect(sock, task)
    asyncio.run(run())


def test_flow_control_limits_sent_audio_and_barge_in_unblocks_it(setup, monkeypatch):
    async def speak(*args, **kwargs):
        return Speech(b'\1\0'*16000*5,16000,0)
    monkeypatch.setattr(setup,'speak',speak)
    async def run():
        sock=Socket()
        task=asyncio.create_task(server.ws(sock))
        sock.put(type='start',policy_id='TH-4471-0093',audio_protocol=2,client_turn=1,audio_flow=True)
        begin=await sock.until('audio_begin',client_turn=1)
        assert begin['flow_control']
        async def fill_window():
            while sum((len(x)-12)//2 for x in sock.seen if isinstance(x,bytes)) < 32000:
                await asyncio.sleep(0)
        await asyncio.wait_for(fill_window(),1)
        await asyncio.sleep(.02)
        assert sum((len(x)-12)//2 for x in sock.seen if isinstance(x,bytes))==32000
        assert not any(isinstance(x,dict) and x.get('kind')=='audio_end' for x in sock.seen)
        sock.put(type='barge_in',client_turn=2)
        await sock.until('audio_cancel',client_turn=2)
        await disconnect(sock,task)
    asyncio.run(run())


def test_flow_control_finishes_after_all_samples_and_final_ack(setup, monkeypatch):
    async def speak(*args, **kwargs):
        return Speech(b'\1\0'*16000*3,16000,0)
    monkeypatch.setattr(setup,'speak',speak)
    async def run():
        sock=Socket()
        task=asyncio.create_task(server.ws(sock))
        sock.put(type='start',policy_id='TH-4471-0093',audio_protocol=2,client_turn=1,audio_flow=True)
        begin=await sock.until('audio_begin',client_turn=1)
        key={'generation':begin['generation'],'audio_id':begin['audio_id']}
        sock.put(type='playback_done',**key)  # premature completion is ignored
        samples=0
        while True:
            item=await asyncio.wait_for(sock.output.get(),2)
            if isinstance(item,bytes):
                samples+=(len(item)-12)//2
                sock.put(type='audio_consumed',samples=samples,**key)
            elif item.get('kind')=='audio_end':
                break
            else:
                assert item.get('kind')!='response_done'
        assert samples==48000
        assert not any(isinstance(x,dict) and x.get('kind')=='response_done' for x in sock.seen)
        sock.put(type='playback_done',underrun_ms=0,**key)
        await sock.until('response_done',client_turn=1)
        await disconnect(sock,task)
    asyncio.run(run())
