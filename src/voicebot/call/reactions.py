"""What the caller asks of us beyond "carry on with the script".

The script has seven turns; a caller does not. They ask you to say it again,
they tell you they are driving, or they answer a question with a grunt.
Advancing regardless is the single thing that makes a bot sound like a bot,
so these cases are read off the caller's own words before the script gets its
turn.

Everything here is keyword matching, deliberately. These decisions gate what
the bot says next on a live call, so they have to be inspectable and testable,
not a model's guess that varies between runs.
"""
from __future__ import annotations

import re

# Phrases that mean "say that again" wherever they appear in the utterance.
_REPEAT_PHRASES = (
    "repeat", "say that again", "say it again", "say again", "come again",
    "one more time", "didn't catch", "did not catch", "didnt catch",
    "not clear", "unclear", "can't hear", "cannot hear", "cant hear",
    "couldn't hear", "breaking up", "speak up", "speak louder", "louder",
    "what was that", "what did you say", "come back again",
    "再说一次", "再讲一次", "重复", "重複", "听不清", "聽不清", "没听清", "沒聽清",
    "大声一点", "大聲一點", "什么意思", "什麼意思",
)

# On their own these mean "I didn't get that". Inside a longer sentence they
# usually do not — "sorry, yes, speaking" is an answer, not a request to
# repeat — so they only count in a short utterance.
_REPEAT_ALONE = ("sorry", "pardon", "what", "huh", "hah", "excuse me", "come again",
                 "sorry ah", "har", "你说什么", "你說什麼", "啊")

# "slower" and "slowly" on their own: the recogniser produces "can you speak
# a a slower?" often enough that a phrase list never catches it, and there is
# no other reason to say the word on a renewal call.
_SLOWER_PHRASES = (
    "slower", "slowly", "slow down", "too fast", "too quick", "slow lah",
    "说慢一点", "說慢一點", "慢一点", "慢一點", "讲慢一点", "講慢一點", "太快", "慢慢",
)

_CALLBACK_PHRASES = (
    "call me later", "call back later", "call again later", "call me back",
    "another time", "not a good time", "bad time", "not convenient",
    "i'm busy", "im busy", "i am busy", "busy now", "busy right now",
    "driving", "in a meeting", "at work now", "can you call", "later can",
    "now not convenient", "现在不方便", "在开车", "在開車", "在忙", "等下再打",
    "待会再打", "晚点再打", "晚點再打", "现在不方便讲", "过后再打",
)

_IMPATIENT = ("anything", "what do you want", "what is it", "what you want",
              "make it quick", "be quick", "hurry", "quickly", "get to the point",
              "how long", "i'm in a rush", "im in a rush", "什么事", "什麼事",
              "有什么事", "有什麼事", "快点", "快點")

_ASSENT = ("yes", "yeah", "yep", "ya", "speaking", "that's me", "thats me",
           "correct", "right", "sure", "ok", "okay", "对", "是", "是的", "我是")

_HESITANT = ("uh", "um", "erm", "hmm", "mmm", "eh", "呃", "嗯")

# Picking up the phone and saying "hello? testing?" is what people do when
# they are not sure the line is working. Answering that with "sorry, I didn't
# catch that" — twice — is the machine failing the easiest turn in the call.
_GREETING = ("hello", "hallo", "halo", "helo", "hi", "hey", "yo", "testing",
             "test test", "can you hear", "you there", "anyone there",
             "喂", "你好", "哈啰", "听得到", "聽得到")


def is_greeting(text: str) -> bool:
    low = text.lower().strip()
    return _words(low) <= 8 and any(_has(low, g) for g in _GREETING)


def _has(text: str, needle: str) -> bool:
    """Word-boundary match for latin tokens, plain containment for CJK.

    Without the boundary "ya" matches "Malaysia" and "ok" matches "broke" —
    the same false positive that once waved an unverified caller past the
    identity gate.
    """
    if needle.isascii():
        return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", text) is not None
    return needle in text


def _words(text: str) -> int:
    latin = len(re.findall(r"[a-z']+", text))
    return latin + len(re.findall(r"[一-鿿]", text))


def _short_enough_to_be_an_answer(text: str) -> bool:
    """Short enough to be answering a question rather than asking one.

    Counted per script. `_words` treats one Chinese character as one word,
    which is the right conservative default everywhere else but made
    "可以可以请安排" — yes, please arrange it — measure seven words, fail the
    five-word test, and an offer to have a colleague call the customer was
    dropped on the floor.
    """
    # Fillers do not make a reply longer: "uh yes, that is my home" is five
    # words of answer with an "uh" in front, and counting the "uh" put it
    # over the line and sent a yes to the model.
    latin = len([w for w in re.findall(r"[a-z']+", text) if w not in _FILLER])
    cjk = len([ch for ch in re.findall(r"[一-鿿]", text) if ch not in _CJK_FILLER])
    return latin <= 5 and cjk <= 8


def wants_repeat(text: str) -> bool:
    """True when the caller is asking to hear the last line again."""
    low = text.lower().strip()
    if any(_has(low, p) for p in _REPEAT_PHRASES):
        return True
    # A bare "sorry?" is a request; "sorry, yes, that's me" is an answer, and
    # "what's covered under renovation?" is a question about the policy.
    if any(_has(low, a) for a in _ASSENT):
        return False
    return _words(low) <= 3 and any(_has(low, p) for p in _REPEAT_ALONE)


def wants_callback(text: str) -> bool:
    """True when the caller is telling us this is the wrong moment.

    On an outbound call this matters more than anything else in this module:
    talking over someone who has said they are driving is how a servicing
    call becomes a complaint.
    """
    low = text.lower().strip()
    return any(_has(low, p) for p in _CALLBACK_PHRASES)


# The first question anyone asks an unexpected caller. Ignoring it is how a
# servicing call starts sounding like a scam call.
_WHO_PHRASES = (
    "who are you", "who is this", "who's this", "whos this", "who am i speaking",
    "who is calling", "who's calling", "whos calling", "where are you calling from",
    "which company", "what company", "what is this about", "what's this about",
    "whats this about", "what is this regarding", "why are you calling",
    "who you", "you are who", "sorry you are", "may i know who",
    "你是谁", "你是誰", "哪位", "什么公司", "什麼公司", "哪里打来", "哪裡打來",
)

# "什么事" is not "who are you" — it is "what do you want", and answering it
# with a self-introduction leaves the caller none the wiser about why their
# phone rang. Asked twice in one call, it produced the same introduction
# twice.
_PURPOSE_PHRASES = (
    "what is this about", "what's this about", "whats this about",
    "what is this regarding", "what do you want", "what you want",
    "why are you calling", "why did you call", "what's the matter",
    "what is it about", "what can i do for you", "how can i help you",
    "what happen", "what happened", "what's happening", "whats happening",
    "what is happening", "what's going on", "whats going on", "what is going on",
    "有什么事", "有什麼事", "什么事", "什麼事", "找我什么事", "找我什麼事",
    "为什么打给我", "為什麼打給我", "什么事情", "什麼事情", "有事吗", "有事嗎",
    "什么保险", "什麼保險", "哪个保险", "哪個保險",       # "呃什么保险来的"
)


def asks_purpose(text: str) -> bool:
    """True when the caller wants to know why we are calling."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _PURPOSE_PHRASES)


def asks_who_we_are(text: str) -> bool:
    """True when the caller wants to know who is on the line.

    "Why are you calling" reads as both this and a question about the
    purpose. The purpose is the more useful answer and wins.
    """
    low = text.lower().strip()
    if asks_purpose(low):
        return False
    return any(_has(low, p) for p in _WHO_PHRASES)


# "You didn't hear me." Answering that with "sorry, I didn't quite catch that"
# is the machine confirming the complaint. It needs its own reply and, more to
# the point, a different approach — repeating a failed strategy is what earns
# the complaint in the first place.
_FRUSTRATED = (
    "you didn't hear", "you did not hear", "you didnt hear", "not listening",
    "you're not listening", "aren't you listening", "i already said",
    "i already told", "i just said", "that's not what i said",
    "thats not what i said", "you got it wrong", "wrong again",
    "listen to me", "are you listening", "no no no", "i said",
    "for the third time", "again and again", "useless",
    "你没听到", "你沒聽到", "你没在听", "我说过了", "我說過了", "我刚才说了",
    "听不懂吗", "聽不懂嗎",
)

# Asking for a person. Always honoured, immediately.
_WANTS_HUMAN = (
    "speak to a person", "speak to someone", "talk to a person",
    "talk to someone", "real person", "human being", "a human",
    "an agent", "live agent", "customer service", "your supervisor",
    "a manager", "someone else", "your colleague", "transfer me",
    "put me through", "get me someone", "i want a person",
    "真人", "人工", "转接", "轉接", "找人", "客服", "你的同事", "你的主管",
)


def sounds_frustrated(text: str) -> bool:
    """True when the caller is telling us we are not getting it."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _FRUSTRATED)


def asks_for_human(text: str) -> bool:
    """True when the caller wants a person. Never argued with."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _WANTS_HUMAN)


# Someone reading out an address: letters one at a time, digits, "dot", "at".
# The sub-dialogue used to abandon a caller mid-spelling and then treat the
# rest of their address as an unintelligible interruption.
_DICTATION = ("dot", "at", "underscore", "dash", "hyphen", "com", "sg", "net",
              "org", "gmail", "yahoo", "hotmail", "outlook", "capital",
              "small", "lowercase", "uppercase", "点", "at符号")

_NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                 "eight", "nine", "oh")


def still_dictating(text: str) -> bool:
    """True when the caller is still spelling something out to us.

    Single letters and number words carry no meaning to any other handler, so
    a reply made mostly of them belongs to whichever sub-dialogue asked for it
    — not to the "I didn't understand you" path.
    """
    low = text.lower()
    if "@" in low:
        return True
    tokens = re.findall(r"[a-z']+|[0-9]+", low)
    if not tokens:
        return False
    spelled = sum(1 for w in tokens
                  if len(w) == 1 or w.isdigit() or w in _NUMBER_WORDS
                  or w in _DICTATION)
    return spelled >= 2 and spelled >= len(tokens) * 0.4


def wants_slower(text: str) -> bool:
    """True when the caller is asking us to slow down.

    Checked before `wants_repeat`, because the two overlap — "sorry, can you
    say that again slower?" is both, and the slower part is the one that
    stops it happening a third time.
    """
    low = text.lower().strip()
    return any(_has(low, p) for p in _SLOWER_PHRASES)


# Words that make a reply an answer to something: assent, refusal, a question,
# or a number. An utterance with none of them, and nothing else this module
# recognises, is one we did not understand — most often a mis-recognition.
_MEANINGFUL = _ASSENT + _IMPATIENT + (
    "no", "nope", "not", "never", "don't", "dont", "cannot", "can't", "wrong",
    "what", "when", "where", "why", "who", "how", "which", "sorry", "please",
    "email", "address", "premium", "policy", "renew", "renewal", "cover",
    "covered", "insurance", "pay", "payment", "thanks", "thank",
    "不", "没", "沒", "要", "吗", "嗎", "什么", "什麼", "怎么", "怎麼",
    "保", "邮", "郵", "电邮", "電郵", "多少", "谢", "謝", "好",
) + _GREETING + _REPEAT_PHRASES + _SLOWER_PHRASES


# A complete answer, whichever language it arrives in. A Singaporean saying
# "是的" in an English call has answered the question — treating that as an
# unreadable line and asking again is the bot failing to understand a word it
# demonstrably knows.
_BARE_YES = ("yes", "yeah", "yep", "ya", "yah", "ok", "okay", "okok", "sure",
             "correct", "right", "true", "confirm", "confirmed", "alright",
             "can", "got it", "noted", "please", "please do", "go ahead",
             "是", "是的", "对", "对的", "對", "對的", "好", "好的", "好啊", "可以",
             "嗯", "行", "没错", "沒錯", "要", "正确", "正確", "没问题", "沒問題",
             "对啊", "是啊", "好的好的")

_BARE_NO = ("no", "nope", "nah", "not", "never", "dont", "cannot", "wrong",
            "incorrect", "no need", "不", "不是", "不对", "不對", "没有", "沒有",
            "不用", "不要", "不好")

_FILLER = ("uh", "um", "erm", "ah", "eh", "oh", "hmm", "lah", "lor", "leh", "ah",
           "well", "so", "then", "i", "think", "sorry", "呃", "嗯", "啊", "喔",
           # "okay, sure. thanks." is a yes with a thank-you on the end.
           "thanks", "thank", "谢谢", "謝謝")


#: Chinese fillers written as their own characters rather than words.
_CJK_FILLER = "呃嗯啊喔哦呀嘛哎"

_CJK_ANSWERS = {w: "yes" for w in _BARE_YES if not w.isascii()}
_CJK_ANSWERS.update({w: "no" for w in _BARE_NO if not w.isascii()})
#: Longest first, so 不是 is read as a no rather than a 是 with a 不 in front.
_CJK_BY_LENGTH = tuple(sorted(_CJK_ANSWERS, key=len, reverse=True))


def _cjk_answer(run: str) -> str | None:
    """Whether a run of Chinese is an answer and nothing else.

    A regex sees a Chinese run as one token — "呃对" and "不乱来啊" come out
    whole — so matching it against a word list never fires. This walks it
    instead: "呃对" is a yes, "对对" is the same yes said twice, and
    "不乱来啊" is neither, because 乱 is not part of any answer.
    """
    core = run.strip(_CJK_FILLER)
    seen: set[str] = set()
    i = 0
    while i < len(core):
        for term in _CJK_BY_LENGTH:
            if core.startswith(term, i):
                seen.add(_CJK_ANSWERS[term])
                i += len(term)
                break
        else:
            return None
        if len(seen) > 1:               # "对，不是这个" answers both ways
            return None
    return seen.pop() if len(seen) == 1 else None


def bare_answer(text: str) -> str | None:
    """"yes" / "no" when the whole utterance is just that, else None.

    Filler is stripped first — "ah yes.", "uh, ya lah" and "是的。" are all the
    same answer. Deliberately strict about being the *whole* utterance:
    "不乱来啊" contains a negation particle but is not an answer to anything,
    and treating it as one is how a mis-recognition used to advance the call.

    Every remaining word has to be part of the answer. Matching any one of
    them was how "uh, okay, what happen?" — a caller asking what this call
    is about — was read as a yes and the script moved on without answering.
    """
    low = text.lower().strip()
    tokens = [w for w in re.findall(r"[a-z']+|[一-鿿]+", low) if w not in _FILLER]
    if not tokens or len(tokens) > 3:
        return None
    joined = "".join(tokens) if all(not w.isascii() for w in tokens) else " ".join(tokens)
    if joined in _BARE_YES:             # set phrases: "go ahead", "got it"
        return "yes"
    if joined in _BARE_NO:              # set phrases: "no need"
        return "no"
    seen: set[str] = set()
    for token in tokens:
        got = (_cjk_answer(token) if not token.isascii() else
               "yes" if token in _BARE_YES else
               "no" if token in _BARE_NO else None)
        if got is None:
            return None
        seen.add(got)
    return seen.pop() if len(seen) == 1 else None


def denies(text: str) -> bool:
    """A no, in reply to a question we asked.

    Looser than `bare_answer`, which insists the negation *is* the whole
    utterance. "No, that's my old one" is plainly a no, and treating it as a
    yes meant confirming a callback to a number the caller had just disowned.
    Only sound for text that answers a yes/no question we put.
    """
    if bare_answer(text) == "no":
        return True
    low = text.lower().strip()
    return any(_has(low, w) for w in _BARE_NO)


#: Yes-words that may be found *inside* a longer reply. A single Chinese
#: character cannot: 是 sits in "我的地址是什么" — what is my address — which is
#: a question, and reading it as a yes accepted an offer the caller never
#: answered. Two characters are a word; one is a coincidence.
_YES_INSIDE = tuple(w for w in _BARE_YES if w.isascii() or len(w) >= 2)


def yes_no(text: str) -> str | None:
    """"yes", "no", or None when the caller answered something else entirely.

    None is the important case. A pending question used to be settled by
    whatever came next, so "aiyah so expensive lah" — a complaint about the
    price — was taken as agreement to a callback the caller never asked for.
    """
    plain = bare_answer(text)
    if plain is not None:
        return plain
    if denies(text):
        return "no"
    low = text.lower().strip()
    if _short_enough_to_be_an_answer(low) and any(_has(low, w) for w in _YES_INSIDE):
        return "yes"
    return None


def script_mismatch(text: str, lang: str) -> bool:
    """True when the words are not in the script the call is being held in.

    Objective, unlike the recogniser's language label, which flips to Chinese
    on a one-word English reply often enough to be useless on its own. If the
    caller has genuinely switched language the switch logic handles it on the
    next turn; until then, a line we cannot read is a line we must not treat
    as agreement.
    """
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if lang == "zh":
        return latin > 0 and cjk == 0
    return cjk > 0 and cjk >= latin


# "You should be able to know." Asked for an address we do not have, the
# caller expects us to already hold it. Counting that as a failed attempt at
# spelling burned a try and answered with "say it again, slowly" — which is
# the machine ignoring what they actually said.
_DEFERS = (
    "you should know", "you should be able to know", "you should have",
    "you already have", "you have it", "you have my", "it's on file",
    "its on file", "on your record", "in your record", "check your record",
    "same as before", "same one", "the one you have", "don't you have",
    "dont you have", "你们应该有", "你应该知道", "你们有我的", "跟之前一样",
    "不是有吗", "记录里有",
)


def defers_to_us(text: str) -> bool:
    """True when the caller is telling us we already hold what we asked for."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _DEFERS)


# "Are you a robot?" On an outbound insurance call the only acceptable answer
# is yes. The bot was routing it to "who are you" and replying "I'm calling from
# Etiqa" — a true sentence that answers a different question, which is the
# textbook shape of a dodge. Asked twice on one recorded call.
_BOT_PHRASES = (
    "are you a robot", "are you a bot", "are you a machine", "are you human",
    "are you a real person", "are you real", "is this a robot", "is this a bot",
    "is this a recording", "is this recorded", "is this automated", "an ai",
    "are you ai", "are you an ai", "talking to a machine", "talking to a robot",
    "talking to a computer", "speaking to a robot", "speaking to a machine",
    "you sound like a robot", "is this a real person",
    "机器人", "機器人", "是人吗", "是人嗎", "真人吗", "真人嗎", "录音", "錄音",
    "自动", "自動", "人工智能", "ai吗", "ai嗎",
)


def asks_if_bot(text: str) -> bool:
    """True when the caller is asking whether they are talking to a machine."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _BOT_PHRASES)


# "Stop calling me." A request to be taken off the list is a do-not-call
# instruction, and on this line it has to be recorded against the policy —
# not absorbed as a polite decline of whatever was just offered, which is
# what happened to "可以不要打电话给我了吗".
_DNC_PHRASES = (
    "stop calling", "don't call me", "dont call me", "do not call", "never call",
    "take me off", "remove me from", "remove my number", "off your list",
    "off the list", "unsubscribe", "no more calls", "don't contact me",
    "dont contact me", "do not contact", "stop contacting",
    "不要打电话给我", "不要再打", "不要打给我", "别打电话", "别再打", "别打给我",
    "不要再联络", "不要联络我", "把我删掉", "不要再打来", "别再打来", "不要再来电",
)


def asks_dnc(text: str) -> bool:
    """True when the caller is asking not to be called again."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _DNC_PHRASES)


# Laughter, a sigh, a "hmm" — sound with no words in it. Not off-topic (the
# model's verdict, which produced an offer to escalate to customer care over a
# chuckle) and not a failure to hear; just not an answer yet.
_NONVERBAL = re.compile(
    r"^[\s.,!?~…]*(?:(?:ha|he|hi|ho){2,}|lol|lmao|hmm+|mmm+|hm+|uh+|um+|er+|ah+|oh+|"
    r"哈{2,}|呵{2,}|嘻{2,}|嘿{2,}|嗯+|哦+|噢+|啊+|哎+|唉+)[\s.,!?~…哈呵嘻嘿嗯哦噢啊哎唉]*$",
    re.I)


def is_nonverbal(text: str) -> bool:
    """True when the utterance carries sound but no words."""
    return bool(_NONVERBAL.match(text.strip()))


_NOT_RECEIVED = (
    "didn't receive", "did not receive", "haven't received", "have not received",
    "never received", "not received", "didn't get", "did not get", "haven't got",
    "never got", "nothing came", "no letter", "no email", "no notice",
    "没有收到", "没收到", "沒有收到", "沒收到", "还没收到", "還沒收到", "没有看到",
    "没看到", "没有拿到", "没拿到",
)


def not_received(text: str) -> bool:
    """True when the caller says the renewal notice never arrived."""
    low = text.lower().strip()
    return any(_has(low, p) for p in _NOT_RECEIVED)


def is_uninterpretable(text: str) -> bool:
    """True when a reply carries nothing we can act on.

    The engine used to advance the script on literally any input, so a
    mis-recognition — "不乱来啊" arriving in the middle of an English call —
    pushed the conversation forward as if the caller had agreed to something.
    Short and meaningless is the signature: a long utterance we cannot parse
    is a real question, and belongs in the escalation path instead.
    """
    low = text.lower().strip()
    if not low:
        return True
    if _words(low) > 6:
        return False
    return not any(_has(low, w) for w in _MEANINGFUL)


# Short acknowledgements that make the next scripted line sound like a reply
# rather than a recital. The scripted wording itself is untouched — this is
# improvised text, which is the only layer accommodation is allowed to happen
# in.
BRIDGES: dict[str, dict[str, str]] = {
    "impatient": {"en": "I'll keep this short.", "zh": "我长话短说。"},
    "assent":    {"en": "Thank you.", "zh": "谢谢您。"},
    "hesitant":  {"en": "No problem.", "zh": "没关系。"},
}

SINGLISH_BRIDGES = {
    "I'll keep this short.": "I keep it short ah.",
    "No problem.": "No problem, no problem.",
}


def bridge_kind(text: str) -> str | None:
    """Which acknowledgement, if any, the caller's reply has earned.

    Order matters: "uh, yes, anything?" is impatient first and assenting
    second, and the impatience is the part worth answering.
    """
    low = text.lower().strip()
    if any(_has(low, p) for p in _IMPATIENT):
        return "impatient"
    # A filler in front of a clear yes is a filler, not hesitation: "呃是的是的"
    # is the caller confirming twice, and answering it with "没关系" ("never
    # mind") reads as though we heard a refusal.
    if (any(_has(low, p) for p in _HESITANT) and _words(low) <= 6
            and not any(_has(low, p) for p in _ASSENT)):
        return "hesitant"
    if any(_has(low, p) for p in _ASSENT):
        return "assent"
    return None


def all_lines() -> list[tuple[str, str]]:
    """(text, lang) for every fixed line here, so the pre-render pass can warm
    them and a live call never pays to synthesise one."""
    out: list[tuple[str, str]] = []
    for kind in BRIDGES.values():
        for lang, text in kind.items():
            out.append((text, lang))
            if lang == "en" and text in SINGLISH_BRIDGES:
                out.append((SINGLISH_BRIDGES[text], "en"))
    return out
