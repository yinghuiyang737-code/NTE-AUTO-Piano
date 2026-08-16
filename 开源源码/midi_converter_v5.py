from collections import Counter, defaultdict
from pathlib import Path
import mido

NATURAL_PC = {0, 2, 4, 5, 7, 9, 11}
MELODY_21 = (67, 69, 71, 72, 74, 76, 77, 79, 81, 83)
ACCOMP_21 = (48, 50, 52, 53, 55, 57, 59, 60, 62, 64, 65)
MELODY_36 = tuple(range(67, 84))
ACCOMP_36 = tuple(range(48, 67))


def _best_shift(notes):
    counts = Counter(n % 12 for n in notes)
    candidates = []
    for shift in range(-6, 6):
        score = sum(count for pc, count in counts.items() if (pc + shift) % 12 in NATURAL_PC)
        candidates.append((score, -abs(shift), shift))
    return max(candidates)[2]


def _fit(note, shift, melody, target):
    note += shift
    if target == 36:
        pool = MELODY_36 if melody else ACCOMP_36
    else:
        pool = MELODY_21 if melody else ACCOMP_21
    while note < pool[0]: note += 12
    while note > pool[-1]: note -= 12
    return min(pool, key=lambda p: (abs(p-note), p))


def convert_midi(source, destination, target=21):
    if target not in (21, 36):
        raise ValueError("目标键数只能是 21 或 36")
    mid = mido.MidiFile(source, clip=True)
    notes, meta, final_tick = [], [], 0
    for track in mid.tracks:
        tick = 0
        active = defaultdict(list)
        for msg in track:
            tick += msg.time; final_tick = max(final_tick, tick)
            if msg.is_meta and msg.type in ("set_tempo", "time_signature", "key_signature"):
                meta.append((tick, msg.copy(time=0)))
            elif hasattr(msg, "channel") and msg.channel == 9:
                continue
            elif msg.type == "note_on" and msg.velocity > 0:
                active[(msg.channel, msg.note)].append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on") and (msg.type == "note_off" or msg.velocity == 0):
                starts = active.get((msg.channel, msg.note))
                if starts:
                    start, velocity = starts.pop(0)
                    notes.append((start, max(start+1, tick), msg.note, velocity))
    if not notes:
        raise ValueError("MIDI 中没有可转换的旋律音符")
    shift = _best_shift([n[2] for n in notes]) if target == 21 else 0
    by_start = defaultdict(list)
    for note in notes: by_start[note[0]].append(note)
    selected = []
    for chord in by_start.values():
        chord.sort(key=lambda n: n[2])
        melody = chord[-1] if chord[-1][2] >= 60 else None
        accompaniment = chord[:-1] if melody else chord
        if len(accompaniment) > 3:
            accompaniment = [accompaniment[0], accompaniment[-2], accompaniment[-1]]
        selected.extend((n, False) for n in accompaniment)
        if melody: selected.append((melody, True))
    events = [(t, 0, m) for t, m in meta]
    events += [(0,0,mido.Message("program_change",channel=3,program=0,time=0)),
               (0,0,mido.Message("program_change",channel=15,program=0,time=0))]
    melody_count = accompaniment_count = 0
    for (start,end,note,velocity), melody in selected:
        channel = 15 if melody else 3
        mapped = _fit(note, shift, melody, target)
        events.append((start,2,mido.Message("note_on",channel=channel,note=mapped,velocity=velocity,time=0)))
        events.append((end,1,mido.Message("note_off",channel=channel,note=mapped,velocity=0,time=0)))
        if melody: melody_count += 1
        else: accompaniment_count += 1
    events.sort(key=lambda e:(e[0],e[1]))
    out=mido.MidiFile(type=0,ticks_per_beat=mid.ticks_per_beat)
    track=mido.MidiTrack(); out.tracks.append(track); last=0
    for tick,_,msg in events:
        track.append(msg.copy(time=tick-last)); last=tick
    track.append(mido.MetaMessage("end_of_track",time=max(0,final_tick-last)))
    out.save(Path(destination))
    return {"target":target,"shift":shift,"melody":melody_count,
            "accompaniment":accompaniment_count,"seconds":out.length}


def convert_to_21key(source, destination):
    return convert_midi(source, destination, 21)


def convert_to_36key(source, destination):
    return convert_midi(source, destination, 36)
