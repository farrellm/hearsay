"""Extract publisher transcripts into data/<id>/reference.txt (plain text, speech only)."""
import html, re, subprocess, pathlib

D = pathlib.Path(__file__).parent / "data"


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s))


def interview():
    s = (D / "interview/page.html").read_text()
    s = s[s.find('id="audio-highlights"') + len('id="audio-highlights">AUDIO TRANSCRIPT'):]
    s = s[:s.find("</table>")]
    s = re.sub(r"<td valign=\"top\">\d+:\d+</td>", " ", s)
    t = strip_tags(s)
    t = re.sub(r"\[Recording date:[^\]]*\]", " ", t)
    t = re.sub(r"(Russ Roberts|Angela Duckworth):", " ", t)
    return t.replace("Intro.", " ").replace("Podcast Episode Highlights", " ").replace("Time", " ", 1)


def produced():
    s = (D / "produced/page.html").read_text()
    s = s[s.find('>', s.find('class="transcript storytext')) + 1:]
    s = s[:s.find("</div>")]
    s = re.sub(r'<b class="icn-story-transcript-wrap">.*?</b>', " ", s, flags=re.S)
    s = re.sub(r"<p class=\"disclaimer\">.*", " ", s, flags=re.S)
    s = re.sub(r"<p>\s*[A-Z][A-Z .'\-]+:", "<p> ", s)  # <p>SPEAKER NAME:
    t = strip_tags(s)
    t = re.sub(r"\([A-Z ,'\-]+\)", " ", t)  # (SOUNDBITE OF MUSIC)
    return t


def music():
    t = subprocess.run(["pdftotext", str(D / "music/ref.pdf"), "-"], capture_output=True, text=True).stdout
    t = re.sub(r"\(“[^)]*\)|\([^)]*\)", " ", t)  # (“Mutual Slump” by DJ SHADOW)
    t = re.sub(r"^\s*(Song Exploder|DJ Shadow - Mutual Slump|Episode 91)\s*$", " ", t, flags=re.M)
    t = re.sub(r"^\s*\w+:\s", " ", t, flags=re.M)
    return t


def spanish():
    t = (D / "spanish/feed_transcript.txt").read_text()
    t = re.sub(r"^\d\d:\d\d:\d\d\s*$", " ", t, flags=re.M)
    return re.sub(r"Speaker \d+:", " ", t)


for name, fn in [("interview", interview), ("produced", produced), ("music", music), ("spanish", spanish)]:
    txt = re.sub(r"\s+", " ", fn()).strip()
    (D / name / "reference.txt").write_text(txt)
    print(name, len(txt.split()), "words |", txt[:100], "...", txt[-80:])
