"""Test documents for the document engine: synthetic pages in the shape of the real ones (the RBI's old-site press
release template; FOMC statements; a press-conference transcript as pypdf returns its pages). Written for the tests,
not copied from the sources; real pages stay out of the repo (CLAUDE.md §4)."""
import random

RBI_TITLE = "Monetary Policy Statement, 2026-27 Resolution of the Monetary Policy Committee (MPC) October 5-7, 2026"


def rbi_page(body: str, *, title: str = RBI_TITLE, charset: str = "utf-8", day: str = "Oct 07, 2026") -> str:
    """The RBI's old press-release template around a body (BS_PressReleaseDisplay.aspx, checked 23 Oct 2026)."""
    return f"""<!DOCTYPE html><html><head><meta charset="{charset}"><title>Reserve Bank of India</title>
<style>table.tablebg td {{ border: 1px solid #efd1aa; }}</style><script>var theForm = document.forms['form1'];</script>
</head><body><form id="form1"><div class="aspNetHidden"><input type="hidden" name="__VIEWSTATE" value="x" /></div>
<h1 class="page_title">Press Releases</h1><div><div id="doublescroll">
<table width="100%" border="0" role="presentation">
<table class="tablebg" cellpadding="0" cellspacing="0" width="100%">
<tr><td class="tableheader"><a href="https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR1.PDF"><img alt="PDF - {title}" src="../Images/pdf.gif"></a> (<span aria-hidden="true">322 kb</span>)</td></tr>
<tr><td align="right" class="tableheader"><b> Date : {day}</b></td></tr>
<tr><td align="center" class="tableheader"><b>{title}</b></td></tr>
<tr class="tablecontent1"><td><table width="100%" border="0" align="center" class="td"><tr><td>
{body}
</td></tr></table></font></td></tr></table></table></div></div>
<div class="grid_1 archives alpha"><h2 class="accordionButton year">2026</h2><ul><li><a href="#">January</a></li></ul></div>
<div class="footer">© Reserve Bank of India. All Rights Reserved.</div></form></body></html>"""


def rbi_view_page(body: str, *, title: str) -> str:
    """The old site's bs_viewcontent.aspx template around a body (the Governor's statements to Oct 2020, the
    transcripts to Apr 2022; checked 24 Oct 2026): the PDF link, a <th> title and the body row, no date row."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Reserve Bank of India</title></head><body>
<form id="form1"><div class="container_12"><div class="grid_12 omega"><div class="text1" id="example-min"><div>
<table width="100%" border="0" cellpadding="0" cellspacing="0" class="tablebg">
<tr><td class="tableheader"><a href="https://rbidocs.rbi.org.in/rdocs/Content/PDFs/X.PDF"><img alt="PDF - {title}"
src="../Images/pdf.gif"></a> (<span aria-hidden="true">174 kb</span>)</td></tr><tr><th>{title}</th></tr>
<tr><td><table width="100%" border="0" align="center" class="td"><tr><td>{body}</td></tr></table></td></tr>
</table></div></div></div></div><div class="footer">© Reserve Bank of India. All Rights Reserved.</div></form></body></html>"""


def rbi_speech_page(body: str, *, title: str, day: str) -> str:
    """The old site's BS_SpeechesView.aspx template around a body (the transcripts from Jun 2022; checked 24 Oct 2026):
    the press release's header rows (PDF link, date, title) above a tablecontent2 row."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Reserve Bank of India</title></head><body>
<form id="form1"><div><table class="tablebg" cellpadding="0" cellspacing="0" width="100%">
<tr><td class="tableheader"><a href="https://rbidocs.rbi.org.in/rdocs/Speeches/PDFs/X.PDF"><img alt="PDF"
src="../Images/pdf.gif"></a> (<span aria-hidden="true">209 kb</span>)</td></tr>
<tr><td align="right" class="tableheader"><b> Date : {day}</b></td></tr>
<tr><td align="center" class="tableheader"><b>{title}</b></td></tr>
<tr class="tablecontent2"><td>{body}</td></tr></table></div></form></body></html>"""


def rbi_statement_body(opening: str, rng: random.Random | None = None, *, press_release: str | None = None) -> str:
    """A Governor's statement: an opening paragraph, then numbered ones and a heading, as the RBI sets them."""
    rng = rng or random.Random(5)
    paras = [f"<p>{k}. {_paragraph(rng, rng.randint(4, 8))}</p>" for k in range(2, 12)]
    tail = f'<p class="head">Press Release: {press_release}</p>' if press_release else ""
    return (f"<p>{opening}</p>{''.join(paras[:4])}<p><b>Decisions and Deliberations of the Monetary Policy Committee "
            f"(MPC)</b></p>{''.join(paras[4:])}{tail}")


def _turn_text(rng: random.Random) -> str:
    return _paragraph(rng, rng.randint(2, 6))


def rbi_transcript_view(turns: list[str], *, date_line: str | None, participants: list[str],
                        rng: random.Random | None = None) -> str:
    """A viewcontent-era transcript body: the date line (to Feb 2020), the participants, then a nested table of turns,
    a name cell ("Shaktikanta Das:") beside each turn's paragraphs. `turns`: the speakers' labels, in order."""
    rng = rng or random.Random(13)
    head = (f"<p>{date_line}</p>" if date_line else "") + "<p><b>Participants from RBI:</b></p><p>" + "<br>".join(
        participants) + "</p>"
    rows = "".join(f'<tr><td class="head">{who}</td><td><p>{_turn_text(rng)}</p><p>{_turn_text(rng)}</p></td></tr>'
                   for who in turns)
    return f'<table class="tablebg"><tr><td colspan="2">{head}</td></tr>{rows}</table>'


def rbi_transcript_speech(turns: list[str], *, participants: list[str], rng: random.Random | None = None) -> str:
    """A speech-era transcript body: the participants list, the moderator's line, then each turn as a paragraph whose
    first line is the speaker's label."""
    rng = rng or random.Random(17)
    head = ("<p>Participants from the Reserve Bank of India:<br>" + "<br>".join(participants) + "</p>"
            "<p>Moderator:<br>Shri Puneet Pancholy – Chief General Manager, Reserve Bank of India</p>")
    return head + "".join(f"<p>{who}<br>{_turn_text(rng)}</p><p>{_turn_text(rng)}</p>" for who in turns)


_RNG = random.Random(20261023)
_TOPICS = ["headline inflation", "core inflation", "food prices", "the rupee", "bank credit", "the fiscal deficit",
           "capital flows", "crude oil prices", "rural demand", "urban consumption", "services exports",
           "industrial activity", "the monsoon", "liquidity conditions", "money market rates"]
_VERBS = ["moderated", "edged up", "remained steady", "eased", "firmed up", "stayed elevated", "softened"]


def _sentence(rng: random.Random) -> str:
    t, v = rng.choice(_TOPICS), rng.choice(_VERBS)
    n = rng.randint(1, 9)
    shapes = [
        (f"{t.capitalize()} {v} in Q{rng.randint(1, 4)}:{2026 + rng.randint(0, 1)}-{27 + rng.randint(0, 1)}, "
         f"and the MPC expects it at {n}.{rng.randint(0, 9)} per cent."),
        f"Dr. Poonam Gupta noted that {t} {v} while Prof. Ram Singh stressed the risks from {rng.choice(_TOPICS)}.",
        f"The U.S. Federal Reserve's actions and {t} weigh on the outlook; the MPC will monitor them closely.",
        f"Real GDP growth for 2026–27 is projected at {n}.{rng.randint(0, 9)} per cent, with risks evenly balanced.",
        f"Shri Sanjay Malhotra observed that “{t} {v}” — a shift the MPC weighed carefully.",
        f"The rupee traded near ₹{80 + rng.randint(0, 20)}.{rng.randint(10, 99)} per US dollar as {t} {v}.",
    ]
    return rng.choice(shapes)


def _paragraph(rng: random.Random, n: int) -> str:
    return " ".join(_sentence(rng) for _ in range(n))


def rbi_resolution_body(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(7)
    paras = [f"<p>{k}. {_paragraph(rng, rng.randint(3, 7))}</p>" for k in range(2, 9)]
    return f"""<p>On the basis of an assessment of the current and evolving macroeconomic situation, the Monetary Policy
Committee (MPC) at its meeting today (October 7, 2026) decided to:</p>
<ul><li>increase the policy repo rate under the liquidity adjustment facility (LAF) by 25 basis points to 5.50 per&nbsp;cent with immediate effect.</li></ul>
<p>Consequently, the standing deposit facility (SDF) rate stands adjusted to 5.25 per cent and the marginal standing
facility (MSF) rate and the Bank Rate to 5.75 per cent.</p>
<p><b>Monetary Policy Decisions</b></p>
<p>The MPC also decided to change the stance to &lsquo;calibrated tightening&rsquo;.<!-- an editor's comment --></p>
{''.join(paras[:3])}
<p><strong>Growth and Inflation Outlook</strong></p>
{''.join(paras[3:5])}
<table width="65%" border="0" class="tablebg"><tr><td class="head">Projection</td><td class="head">2026-27</td></tr>
<tr><td>CPI inflation</td><td>4.8&nbsp;per cent</td></tr><tr><td>Real GDP growth</td><td>6.5 per cent</td><td></td></tr></table>
<h3>Rationale for Monetary Policy Decisions</h3>
{''.join(paras[5:])}
<p>9. Dr. Nagesh Kumar and Prof. Ram Singh voted to keep the policy repo rate unchanged at 5.25 per cent.
Shri Sanjay Malhotra, Dr. Poonam Gupta, Shri Indranil Bhattacharyya and Dr. Rajiv Ranjan voted to increase it by 25 basis points.</p>
<p>10. The minutes of the MPC's meeting will be published on October 21, 2026.<span style="display: none">hidden text</span><span class="sr-only">(opens in a new window)</span></p>
<p>11. The next meeting of the MPC is scheduled during December 2 to 4, 2026.</p>
<p align="right"><span class="head">(Puneet Pancholy)</span><br>Chief General Manager</p>
<p class="head">Press Release: 2026-2027/1234</p>"""


MEMBERS = ["Dr. Nagesh Kumar", "Prof. Ram Singh", "Dr. Poonam Gupta", "Shri Indranil Bhattacharyya",
           "Dr. Rajiv Ranjan", "Shri Sanjay Malhotra"]


def rbi_minutes_body(rng: random.Random | None = None) -> str:
    rng = rng or random.Random(11)
    parts = ["<p><b>Minutes of the Monetary Policy Committee Meeting, October 5 to 7, 2026</b></p>",
             f"<p>{_paragraph(rng, 4)}</p>",
             "<p><b>Voting on the Resolution to increase the policy repo rate by 25 basis points</b></p>",
             "<table class=\"tablebg\"><tr><th>Member</th><th>Vote</th></tr>"
             + "".join(f"<tr><td>{m}</td><td>{'No' if i < 2 else 'Yes'}</td></tr>" for i, m in enumerate(MEMBERS))
             + "</table>"]
    for m in MEMBERS:
        parts.append(f"<p><b>Statement by {m}</b></p>")
        parts += [f"<p>{k}. {_paragraph(rng, rng.randint(4, 9))}</p>" for k in range(rng.randint(2, 5))]
    parts.append("<p><b>Annex</b></p><p>The schedule of the next meetings follows the RBI Act, 1934.</p>")
    return "".join(parts)


FOMC_A = (
    "Recent indicators suggest that growth of economic activity has moderated. Job gains have slowed, and the "
    "unemployment rate has edged up but remains low. Inflation has moved up and remains somewhat elevated.\n\n"
    "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. "
    "Uncertainty about the economic outlook remains elevated. The Committee is attentive to the risks to both sides "
    "of its dual mandate.\n\n"
    "In support of its goals, the Committee decided to maintain the target range for the federal funds rate at "
    "4-1/4 to 4-1/2 percent. In considering the extent and timing of additional adjustments to the target range, the "
    "Committee will carefully assess incoming data, the evolving outlook, and the balance of risks. The Committee "
    "will continue reducing its holdings of Treasury securities and agency debt and agency mortgage-backed securities.\n\n"
    "Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice Chair; Michael S. Barr; "
    "Michelle W. Bowman; Lisa D. Cook; and Christopher J. Waller.")

FOMC_B = (
    "Recent indicators suggest that growth of economic activity has moderated further. Job gains have slowed, and "
    "the unemployment rate has edged up but remains low. Inflation has moved up and remains somewhat elevated.\n\n"
    "The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. "
    "The Committee is attentive to the risks to both sides of its dual mandate and judges that downside risks to "
    "employment have risen.\n\n"
    "In support of its goals and in light of the shift in the balance of risks, the Committee decided to lower the "
    "target range for the federal funds rate by 1/4 percentage point to 4 to 4-1/4 percent. In considering additional "
    "adjustments to the target range, the Committee will carefully assess incoming data, the evolving outlook, and the "
    "balance of risks. The Committee will continue reducing its holdings of Treasury securities. It will continue "
    "reducing its holdings of agency debt and agency mortgage-backed securities.\n\n"
    "Voting for the monetary policy action were Jerome H. Powell, Chair; John C. Williams, Vice Chair; Michael S. Barr; "
    "Michelle W. Bowman; Lisa D. Cook; and Christopher J. Waller. Voting against this action was Stephen I. Miran, who "
    "preferred to lower the target range by 1/2 percentage point at this meeting.")


def transcript_pages() -> list[str]:
    """A press-conference transcript as pypdf returns it: one string per page, a running header and a page footer."""
    header = "October 28, 2026 Chair Powell's Press Conference FINAL"
    body = [
        "CHAIR POWELL. Good afternoon. My colleagues and I remain squarely focused on achieving our",
        "dual-mandate goals of maximum employment and stable prices for the benefit of the American",
        "people. Today, the Federal Open Market Committee decided to lower our policy interest rate by",
        "1/4 percentage point.",
        "",
        "I'll come back to monetary policy after briefly reviewing economic develop-",
        "ments. The economy has been growing at a moderate pace.",
        "MICHELLE SMITH. Thank you. Steve.",
        "STEVE LIESMAN. Steve Liesman, CNBC. Mr. Chair, can you explain the dissent today? And",
        "what would it take for the Committee to cut again in December?",
        "CHAIR POWELL. So, we had a good discussion. I'll start by saying the Committee is not on a",
        "preset course. We'll make our decisions meeting by meeting, as we always do.",
        "We are attentive to the risks to both sides of our mandate.",
    ]
    pages, k = [], 0
    for n, size in enumerate((5, 5, 3), start=1):
        lines = [header] + body[k:k + size] + [f"Page {n} of 3"]
        k += size
        pages.append("\n".join(lines))
    return pages


def tiny_pdf(pages: list[list[str]]) -> bytes:
    """A minimal PDF with one text line per entry, for pypdf to read back."""
    def esc(s):
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for lines in pages:
        content = "BT /F1 11 Tf 14 TL 72 740 Td " + " ".join(f"({esc(ln)}) Tj T*" for ln in lines) + " ET"
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def fed_page(paragraphs: list[str], *, day: str = "September 16, 2026", title: str = "Federal Reserve issues FOMC statement",
             release: str = "For release at 2:00 p.m. EDT", note_date: str | None = None, panel: bool = False) -> str:
    """The Fed's press-release template around a statement (newsevents/pressreleases/monetaryYYYYMMDDa.htm, checked on
    the pages saved 23 Oct 2026): a heading column (date, title, release time, share menu) and a body column ending
    with the media contact (a Cloudflare-obfuscated address) and the implementation-note link. Some pages (15 Mar
    2020) add a related-links panel. Synthetic: written for the tests, not copied from the source."""
    body = "\n".join(f"<p>{p}</p>" for p in paragraphs)
    extra = ('<div class="panel panel-attachments col-xs-12 col-sm-8 col-md-8"><div><ul><li><a href="/x.htm">'
             "Implementation Note issued " + (note_date or day) + "</a></li><li><a href='/y.htm'>Related actions</a>"
             "</li></ul></div></div>") if panel else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Federal Reserve Board - {title}</title>
<script>window.dataLayer = [];</script></head><body><div id="content" class="container">
<div class="row"><div id="article">
<div class="heading col-xs-12 col-sm-8 col-md-8"><p class="article__time">{day}</p><h3 class="title">{title}</h3>
<p class="releaseTime">{release}
<ul class="list-unstyled"><li class="share btn-group"><a href="#">Share</a><ul role="menu"><li><a href="https://twitter.com/share">x</a></li></ul></li></ul>
</div><div class="col-xs-12 col-sm-4 col-md-4 hidden-sm"></div>
<div class="col-xs-12 col-sm-8 col-md-8">
{body}
<p>For media inquiries, please email <a href="/cdn-cgi/l/email-protection#6f"><span class="__cf_email__">[email&#160;protected]</span></a> or call 202-452-2955.</p>
<p><a href="/newsevents/pressreleases/monetary20260916a1.htm">Implementation Note issued {note_date or day}</a></p>
</div>{extra}<div class="col-xs-12 col-sm-4 col-md-4"></div></div></div>
<div class="row"><div id="lastUpdate" class="lastUpdate">Last Update: {day}</div></div></div>
<footer class="container footer"><h6>Board of Governors of the Federal Reserve System</h6></footer></body></html>"""


FED_ECONOMY = (
    ("Recent indicators suggest that economic activity has been expanding at a solid pace. Job gains have remained "
    "low, and the unemployment rate has been little changed in recent months. Inflation remains somewhat elevated."),
    ("The Committee seeks to achieve maximum employment and inflation at the rate of 2 percent over the longer run. "
    "Uncertainty about the economic outlook remains elevated. The Committee is attentive to the risks to both sides "
    "of its dual mandate."),
)
FED_GUIDANCE = (" In considering the extent and timing of additional adjustments to the target range for the federal "
                "funds rate, the Committee will carefully assess incoming data, the evolving outlook, and the balance "
                "of risks. The Committee is strongly committed to supporting maximum employment and returning "
                "inflation to its 2 percent objective.")
FED_MONITOR = (
    "In assessing the appropriate stance of monetary policy, the Committee will continue to monitor the implications "
    "of incoming information for the economic outlook. The Committee would be prepared to adjust the stance of "
    "monetary policy as appropriate if risks emerge that could impede the attainment of the Committee's goals.")


def fed_named(decision: str, voting: str) -> list[str]:
    """A statement in the format used to Apr 2026: economy, goals, decision and guidance, monitoring, voting line."""
    return [*FED_ECONOMY, f"In support of its goals, the Committee {decision}.{FED_GUIDANCE}", FED_MONITOR, voting]


def fed_tally(tally: str, decision: str, against: str | None = None) -> list[str]:
    """A statement in the format used from Jun 2026: the approval line, the decision, two short paragraphs, the
    dissenters (if any)."""
    return [f"The Federal Open Market Committee approved the following statement for release by a {tally} vote:",
            (f"The Committee {decision}, in support of the Federal Reserve's dual mandate. The Committee is continuing "
            "its policy of maintaining ample reserves in the banking system."),
            ("Economic activity is expanding at a solid pace despite elevated uncertainty. Productivity growth and "
            "capital investment are strong. Job gains have kept pace with the workforce, and the unemployment rate has "
            "changed little."),
            ("Inflation remains elevated relative to the Committee's 2 percent goal, in part reflecting supply shocks "
            "that have driven price increases in certain sectors, including energy. The Committee will deliver price "
            "stability."),
            *([against] if against else [])]


def fed_rss(*items: tuple[str, str, str]) -> str:
    """The monetary-policy feed (feeds/press_monetary.xml): (title, link, pubDate) per item, newest first."""
    body = "".join(f"<item><title>{t}</title><link><![CDATA[{u}]]></link><guid><![CDATA[{u}]]></guid>"
                   f"<description><![CDATA[{t}]]></description><category>Monetary Policy</category>"
                   f"<pubDate><![CDATA[{d}]]></pubDate></item>" for t, u, d in items)
    return ('\ufeff<?xml version="1.0" encoding="utf-8" ?><rss version="2.0"><channel>'
            "<title>FRB: Press Release - Monetary Policy</title><link><![CDATA[https://www.federalreserve.gov/feeds/"
            "feeds.htm]]></link><language>en</language>" + body + "</channel></rss>")


# ---------------------------------------------------------------------------------------------- FOMC minutes and
# press-conference transcripts (B5 part 2). Synthetic, in the shapes of the pages saved 23 Oct 2026: written for the
# tests, not copied from the source.
_MINUTES_TOPICS = ["Developments in Financial Markets and Open Market Operations", "Staff Review of the Economic Situation",
                   "Staff Review of the Financial Situation", "Staff Economic Outlook",
                   "Participants' Views on Current Conditions and the Economic Outlook"]


_FED_TOPICS = ["consumer spending", "business investment", "the labor market", "core inflation", "Treasury yields",
               "equity prices", "credit conditions", "housing activity", "money market rates", "the dollar"]


def _fed_sentence(rng: random.Random) -> str:
    """A plain-ASCII sentence about the economy (fits a PDF's Latin-1 text)."""
    a, b = rng.choice(_FED_TOPICS), rng.choice(_FED_TOPICS)
    return (f"Participants noted that {a} {rng.choice(_VERBS)} over the intermeeting period, while {b} was "
            f"{rng.randint(1, 9)}.{rng.randint(0, 9)} percent above its level a year earlier.")


def _minutes_paragraph(k: int) -> str:
    rng = random.Random(4000 + k)
    return " ".join(_fed_sentence(rng) for _ in range(6))


def fed_minutes_page(*, days: str, held: list[str], voting: list[str], closing: str = "", body_paragraphs: int = 18,
                     title: str = "Minutes of the Federal Open Market Committee") -> str:
    """A minutes page (monetarypolicy/fomcminutesYYYYMMDD.htm): the title, the date line and the opening sentence
    naming the meeting's days (`held`: "Tuesday, March 13, 2040"), run-in topic headings, the policy action, the
    voting paragraphs (`voting`: "<strong>Voting for this action:</strong> …" lines, as given), an optional closing
    section (another meeting held since the last minutes), the Secretary's signature and footnotes below a rule."""
    opening = (" and continued on ".join(f"{d}, at {'10:00' if i == 0 else '9:00'} a.m." for i, d in enumerate(held)))
    parts = [(f"<p><strong>{days}</strong><br />\nA joint meeting of the Federal Open Market Committee and the Board of "
              f"Governors of the Federal Reserve System was held in the offices of the Board of Governors on {opening}"
              '<a href="#fn1" title="footnote 1"><sup>1</sup></a><a name="f1"></a></p>')]
    for k in range(body_paragraphs):
        lead = f"<strong>{_MINUTES_TOPICS[k // 4]}</strong><br />\n" if k % 4 == 0 and k // 4 < len(_MINUTES_TOPICS) else ""
        parts.append(f"<p>{lead}{_minutes_paragraph(k)}</p>")
    parts.append("<p><strong>Committee Policy Actions</strong><br />\nAt the conclusion of the discussion, the "
                 "Committee voted to authorize and direct the Federal Reserve Bank of New York to execute transactions "
                 "in the System Open Market Account in accordance with the domestic policy directive. The vote also "
                 "encompassed approval of the statement below for release at 2:00 p.m.:</p>")
    parts.append('<blockquote><p>"The Committee seeks to achieve maximum employment and inflation at the rate of 2 '
                 'percent over the longer run."</p></blockquote>')
    parts += [f"<p>{v}</p>" for v in voting]
    parts.append("<p>It was agreed that the next meeting of the Committee would be held on Tuesday-Wednesday. The "
                 "meeting adjourned at 10:30 a.m.</p>")
    parts.append("<p><strong>Notation Vote</strong><br />\nBy notation vote, the Committee unanimously approved the "
                 "minutes of the previous meeting.</p>")
    if closing:
        parts.append(closing)
    parts.append('<p style="text-align:center">_______________________</p>')
    parts.append('<p style="text-align:center"><strong>Ada B. Clerk</strong><br />\n<strong>Secretary</strong></p>')
    body = "\n\n".join(parts)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Federal Reserve Board - {title}</title>
</head><body><div id="content" class="container"><div class="row">
<div class="col-xs-12 col-sm-4 col-md-3"><a href="/monetarypolicy/fomcminutes.htm">FOMC Minutes</a></div>
<div id="article" class="col-xs-12 col-sm-8 col-md-9">
<h3>{title}</h3>
<!-- Article Area -->
{body}
<hr align="left" size="1" width="33%" />
<p><a name="fn1"></a>1. The Federal Open Market Committee is referenced as the "FOMC" and the "Committee" in these
minutes. <a href="#f1">Return to text</a></p>
</div></div>
<div class="row"><div class='lastUpdate' id="lastUpdate">Last Update: {days}</div></div></div>
<footer class="container footer"><h6>Board of Governors of the Federal Reserve System</h6></footer></body></html>"""


def fed_presconf_page(days: str, pdf: str | None, *, commented: bool = False) -> str:
    """A press-conference page (monetarypolicy/fomcpresconfYYYYMMDD.htm): the video player, then the related
    links: the transcript (`pdf`, its path, once posted; `commented`: present only inside an HTML comment, as on
    16 Sep 2026's page) and the statement and minutes."""
    link = f'<p><a href="{pdf}">Press Conference Transcript (PDF)</a> </p>' if pdf and not commented else ""
    hidden = f"<!--{pdf} -->" if pdf and commented else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>Federal Reserve Board - FOMC Press
Conference</title></head><body><div id="content" class="container"><div class="row">
<div id="article" class="col-xs-12 col-sm-8 col-md-9">
<h3 class="title">{days} FOMC Meeting</h3>
<div class="video"><script>var transcriptLinkLabel = "Transcript"; videojs("frb-video1");</script></div>
{hidden}
<h4>Related Information</h4>
{link}
<p>FOMC Meeting Statement: <a href="/monetarypolicy/files/monetary1a1.pdf">PDF</a> | <a href="/x.htm">HTML</a></p>
<p>Minutes: <a href="/monetarypolicy/files/fomcminutes1.pdf">PDF</a> | <a href="/y.htm">HTML</a></p>
</div></div></div></body></html>"""


def fed_transcript_pdf(day: str, chair: str = "CHAIR LANE", *, turns: int = 40) -> bytes:
    """A press-conference transcript PDF: the running header, the title line naming the day, and speaker turns."""
    header = f"{day} Chair's Press Conference FINAL"
    lines = ["Transcript of Chair Lane's Press Conference", day, ""]
    rng = random.Random(77)
    for k in range(turns):
        who = chair if k % 2 == 0 else ("MICHELLE SMITH" if k % 4 == 1 else "ADA REPORTER")
        lines.append(f"{who}. " + _fed_sentence(rng))
        lines += [_fed_sentence(rng) for _ in range(3)]
        lines.append("")
    pages, size = [], 30
    for n, i in enumerate(range(0, len(lines), size), start=1):
        pages.append([header, f"Page {n} of {(len(lines) + size - 1) // size}", *lines[i:i + size]])
    return tiny_pdf(pages)


# ---------------------------------------------------------------------------------------------- MPC resolutions (B6)
# Synthetic resolutions in the shapes of the saved ones (Dec 2018 to Oct 2026): the old era's "decided to:" list
# (to Dec 2024) and the new era's attendance paragraph (from Feb 2025). The wording of each part is the caller's.
_FILLER = ["Global growth has held up, though trade frictions weigh on the outlook.",
           "Domestic demand remained firm, supported by private consumption and fixed investment.",
           "Financial conditions eased and the rupee traded in a narrow range.",
           "Liquidity in the banking system stayed in surplus through the period.",
           "Services activity strengthened on buoyant domestic and external demand."]


def _fill(k: int, n: int = 4) -> str:
    return " ".join(_FILLER[(k + i) % len(_FILLER)] for i in range(n))


def mpc_title(days: str, fy: str = "2040-41") -> str:
    return f"Monetary Policy Statement, {fy} Resolution of the Monetary Policy Committee (MPC) {days}"


def mpc_old(*, decision: str, consequently: str, stance: str, votes: list[str], projections: list[str] = (),
            today: str = "May 4, 2040", minutes: str = "May 18, 2040", nxt: str = "June 4 to 6, 2040") -> str:
    """The old era's body: the decision list, the consequences, the stance, then numbered paragraphs."""
    paras = [f"<p>{k}. {_fill(k, 5)}</p>" for k in range(2, 6)]
    k = 6
    for p in projections:
        paras.append(f"<p>{k}. {_fill(k, 2)} {p}</p>")
        k += 1
    paras.append(f"<p>{k}. {_fill(k)}</p>")
    k += 1
    for v in votes:
        paras.append(f"<p>{k}. {v}</p>")
        k += 1
    paras += [f"<p>{k}. The minutes of the MPC's meeting will be published on {minutes}.</p>",
              f"<p>{k + 1}. The next meeting of the MPC is scheduled during {nxt}.</p>"]
    return (f"<p>On the basis of an assessment of the current and evolving macroeconomic situation, the Monetary Policy "
            f"Committee (MPC) at its meeting today ({today}) decided to:</p><ul><li>{decision}</li></ul>"
            f"<p>{consequently}</p><p>{stance}</p><p>These decisions are in consonance with the objective of achieving "
            "the medium-term target for consumer price index (CPI) inflation of 4 per cent within a band of +/- 2 per "
            "cent, while supporting growth.</p><p><b>Assessment</b></p>" + "".join(paras)
            + '<p align="right"><span class="head">(Ira Sen)</span><br>Chief General Manager</p>'
            + '<p class="head">Press Release: 2040-2041/123</p>')


def mpc_new(*, no: int, days: str, chair: str, members: list[str], decision: str, stance: str,
            rationale: list[str] = (), projections: list[str] = (), nxt: str = "December 3 to 5, 2040") -> str:
    """The new era's body: the attendance paragraph, the decision paragraph, the outlook and the rationale."""
    listed = ", ".join(members[:-1]) + f" and {members[-1]}"
    paras = [f"<p>{k}. {_fill(k, 5)}</p>" for k in range(3, 7)]
    paras += [f"<p>{k}. {_fill(k, 2)} {p}</p>" for k, p in enumerate(projections, start=7)]
    k = 7 + len(projections)
    for r in rationale:
        paras.append(f"<p>{k}. {r}</p>")
        k += 1
    return ("<p><b>Monetary Policy Decisions</b></p>"
            f"<p>The Monetary Policy Committee (MPC) held its {no}th meeting from {days} under the chairmanship of "
            f"{chair}, Governor, Reserve Bank of India. The MPC members {listed} attended the meeting.</p>"
            f"<p>2. After a detailed assessment of the evolving macroeconomic and financial developments and the outlook, "
            f"the MPC {decision} The MPC also decided to {stance}</p><p><b>Growth and Inflation Outlook</b></p>"
            f"<p><b>Global Outlook</b></p>" + "".join(paras)
            + f"<p>{k}. The next meeting of the MPC is scheduled for {nxt}.</p>"
            + '<p align="right"><span class="head">(Ira Sen)</span><br>Chief General Manager</p>')


# ---------------------------------------------------------------------------------------------- MPC minutes (B6 part 2)
# Synthetic minutes in the shape of the 49 saved (Dec 2018 to Oct 2026): the opening, "Resolution" and the resolution's
# copy, the vote (its heading before the table, or as the table's first row), then each member's statement under a
# plain "Statement by …" paragraph, the signature and the press-release number. Written for the tests, not copied.
def mpc_minutes(*, no: int, days: str, members: list[str], resolution: list[str], heading: str | None,
                rows: list[tuple[str, str] | tuple[str, str, int]], heading_in_table: bool = False,
                magnitude: str = "reduction", statements: list[str] | None = None,
                press_release: str | None = "2040-2041/456") -> str:
    """`resolution`: the copy's paragraphs (the decision, the stance, the vote sentences); `rows`: (member, "Yes" |
    "No"[, magnitude in bps]); `statements`: who speaks (default: `members`)."""
    listed = ", ".join(f"{m}, Member" for m in members[:-1]) + f" and {members[-1]}, Governor"
    paras = [(f"<p>The {no}th meeting of the Monetary Policy Committee (MPC), constituted under section 45ZB of the "
              f"Reserve Bank of India Act, 1934, was held during {days} at the Reserve Bank of India, Mumbai.</p>"),
             f"<p>2. The meeting was attended by all the members – {listed}.</p>",
             ("<p>3. According to Section 45ZL of the Reserve Bank of India Act, 1934, the Reserve Bank shall publish, "
              "on the fourteenth day after every meeting of the Monetary Policy Committee, the minutes of the "
              "proceedings of the meeting.</p>"),
             f"<p>4. {_fill(4, 5)}</p>", "<p>Resolution</p>"]
    paras += [f"<p>{k}. {r}</p>" for k, r in enumerate(resolution, start=5)]
    three = any(len(r) == 3 for r in rows)
    head = "<tr><th>Member</th><th>Vote</th>" + (f"<th>Magnitude of policy repo rate {magnitude} (basis points)</th>"
                                                 if three else "") + "</tr>"
    body = "".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td>" + (f"<td>{r[2]}</td>" if len(r) == 3 else "") + "</tr>"
                   for r in rows)
    first = f'<tr><td colspan="3">{heading}</td></tr>' if heading and heading_in_table else ""
    if heading and not heading_in_table:
        paras.append(f"<p>{heading}</p>")
    paras.append(f'<table border="1" width="60%">{first}{head}{body}</table>')
    for k, m in enumerate(statements if statements is not None else members):
        paras.append(f"<p>Statement by {m}</p>")
        paras += [f"<p>{40 + 6 * k + j}. {_fill(k + j, 5)}</p>" for j in range(6)]
    paras.append('<p align="right"><span class="head">(Ira Sen)</span><br>Chief General Manager</p>')
    if press_release:
        paras.append(f'<p class="head">Press Release: {press_release}</p>')
    return "".join(paras)
