"""Test documents for the document engine: synthetic pages in the shape of the real ones (the RBI's old-site press
release template; FOMC statements; a press-conference transcript as pypdf returns its pages). Written for the tests,
not copied from the sources; real pages stay out of the repo (CLAUDE.md §4)."""
import random

RBI_TITLE = "Monetary Policy Statement, 2026-27 Resolution of the Monetary Policy Committee (MPC) October 5-7, 2026"


def rbi_page(body: str, *, title: str = RBI_TITLE, charset: str = "utf-8") -> str:
    """The RBI's old press-release template around a body (BS_PressReleaseDisplay.aspx, checked 23 Oct 2026)."""
    return f"""<!DOCTYPE html><html><head><meta charset="{charset}"><title>Reserve Bank of India</title>
<style>table.tablebg td {{ border: 1px solid #efd1aa; }}</style><script>var theForm = document.forms['form1'];</script>
</head><body><form id="form1"><div class="aspNetHidden"><input type="hidden" name="__VIEWSTATE" value="x" /></div>
<h1 class="page_title">Press Releases</h1><div><div id="doublescroll">
<table width="100%" border="0" role="presentation">
<table class="tablebg" cellpadding="0" cellspacing="0" width="100%">
<tr><td class="tableheader"><a href="https://rbidocs.rbi.org.in/rdocs/PressRelease/PDFs/PR1.PDF"><img alt="PDF - {title}" src="../Images/pdf.gif"></a> (<span aria-hidden="true">322 kb</span>)</td></tr>
<tr><td align="right" class="tableheader"><b> Date : Oct 07, 2026</b></td></tr>
<tr><td align="center" class="tableheader"><b>{title}</b></td></tr>
<tr class="tablecontent1"><td><table width="100%" border="0" align="center" class="td"><tr><td>
{body}
</td></tr></table></font></td></tr></table></table></div></div>
<div class="grid_1 archives alpha"><h2 class="accordionButton year">2026</h2><ul><li><a href="#">January</a></li></ul></div>
<div class="footer">© Reserve Bank of India. All Rights Reserved.</div></form></body></html>"""


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
