"""Client consumption credits bound audio sent ahead of actual playback."""
import asyncio


class PlayoutWindow:
    def __init__(self, sample_rate):
        self.limit = sample_rate * 2
        self.sent = self.consumed = 0
        self.final = False
        self.changed = asyncio.Event()

    def acknowledge(self, samples):
        if (isinstance(samples, bool) or not isinstance(samples, int)
                or not self.consumed < samples <= self.sent):
            return False
        self.consumed = samples
        self.changed.set()
        return True

    async def reserve(self, samples):
        while self.sent + samples - self.consumed > self.limit:
            self.changed.clear()
            await asyncio.wait_for(self.changed.wait(), 15)
        self.sent += samples
