Subject: Haver in Claude — search, numbers, and charts (three connectors)

Hi all —

You can now use Haver from Claude on the desktop app, on claude.ai, and on your
phone. No Python, no DLX install, no zip files. Three connectors, one Microsoft
sign-in each the first time.

They stack:

  1. haver-metadata   find the ticker
  2. haver-data       pull the actual numbers
  3. haver-chart      rebuild a Haver screenshot in our house style

---

How to add them (once)

In Claude Desktop or claude.ai:  Settings → Connectors → Add custom connector.

Name                         URL
haver-metadata               https://hvr-mcp.work/mcp
haver-data                   https://data.hvr-mcp.work/mcp
haver-chart                  https://chart.hvr-mcp.work/mcp

Leave the OAuth boxes as Claude fills them in. Sign in with your work Microsoft
account when the browser opens.

If sign-in says you are not assigned, ping me — I have to add your name on our
side before the tools will answer.

After that, start a new chat (or a Project) and ask:  “what Haver tools do you have?”
You should see search / get series, get_observations, and resolve + render.

Optional: I can send two short instruction files (or Project text) that stop
Claude guessing tickers. The tools work without them.

---

What each one is for, with prompts you can paste

1) haver-metadata — “what is the ticker for …?”

  Find the unemployment rate in Haver and give me the code@database ticker,
  frequency, and how far back it goes.

  Search for the Philly Fed manufacturing current activity index and show me
  the closest three matches.

It searches our catalog. It does not pull the numbers. The catalog’s end date
is a snapshot — do not treat it as “last observation.”

2) haver-data — “give me the numbers”

  Pull LR@USECON since 2024 and tell me the last three readings and the change
  over the past six months.

  Confirm the ticker for industrial production, pull it since 2015, and
  compute the year-over-year percent change for the last 12 months.

Use this when you want to quote, compare, or do arithmetic in the conversation.
Leave the end date off so you get Haver’s latest, not the catalog’s.

3) haver-chart — “rebuild this screenshot”

Paste a Haver chart and say what you want. Claude finds each series, draws it
in our style, and shows you the picture in the chat. Look at it before you
use it.

  [paste the screenshot]
  Rebuild this in RenMac style. The formula on the chart is zs(yryr%(IP))
  and the other line is the Philly Fed manufacturing current-activity
  diffusion index, also z-scored. Shared axis, recession bands.

If it is unsure which series you mean, it will show you two or three
candidates. Pick one in plain English (“the ISM one, from surveys”) — do not
say “just take the first.”

Then keep talking. Every change is a new chart; the last one is still there.

  Title it “Factory output is lagging the survey”.
  Change the second legend to “Philly Fed current activity”.
  Put the Philly Fed line on the right axis with its own scale.
  Make it year-over-year percent change instead of the level.
  Add recession bands.
  Lag the survey by three months.
  Make the second series bars instead of a line.
  Start it at 2010. Label the x-axis by year.
  The source chart’s last reading for the survey is about 2.4. Does yours match?

If you want a file to drop in a newsletter, ask for the chart link after it
renders.

A few habits that save a round-trip: paste the formula as printed
(zs(yryr%(IP))), not a paraphrase; give the series name as printed, including
ISM / Philly Fed / SA; say if something looks wrong rather than accepting it.

---

How the three fit together

Think of metadata as the card catalog, data as the book, and chart as the
figure you would put in the daily. A typical hop:

  What is the ticker for the Philly Fed current activity index?
  Pull it since 2019 and give me the last four values.
  [paste a Haver screenshot] Rebuild this next to IP, z-scored, with recessions.

You do not have to use all three every time. Search only, or numbers only, or
paste-and-rebuild only, are all fine.

Questions to me.

Aman
