# Recording script — haver-chart (custom connector)

Casual, ~7–8 minutes. Have one real Haver screenshot ready to paste
(IP vs Philly Fed is the worked example). Desktop or claude.ai.

---

**[0:00  Open]**

This is haver-chart. You paste a Haver screenshot, it comes back in our house
style, in the chat. Then you keep talking — title, legends, axes — and it
redraws.

**[0:20  Add the connector]**

Settings, Connectors, Add custom connector.

Name: haver-chart
URL: `https://chart.hvr-mcp.work/mcp`

Same Microsoft sign-in as the other two. No Python on your machine.

**[0:45  Architecture, while it connects]**

Same idea as haver-data, one extra step.

Claude still cannot see Haver. Your prompt goes to a Windows box that has DLX.
That box does two jobs: it *finds* the series the screenshot is talking about —
same rules we use on the daily — and then it *draws* the chart with our style
file. The picture comes back into the chat. The file also lives on that box; if
you want a copy for a newsletter, ask for the link.

It does not write anything into our learning files. What you approve here is
for this conversation, not for tomorrow’s daily.

**[1:20  New chat — tools]**

“What haver-chart tools do you have?”

Two tools: resolve_series and render_chart. Find, then draw. It should never
skip the first one and guess a ticker.

**[1:40  The rebuild — Example A]**

Paste your screenshot, then:

> Rebuild this in RenMac style. The formula on the chart is zs(yryr%(IP))
> and the other line is the Philly Fed manufacturing current-activity
> diffusion index, also z-scored. Shared axis, recession bands.

Say the formula *as printed*. If you paraphrase it, you lose the nesting and
you get the wrong line.

Let it run. You should see a resolve per line, then a render, then a picture
in the chat.

**Look at the picture.** That’s the whole product. On the email pipeline you
approve a ticker list and find out you were wrong when it ships. Here you
approve pixels.

**[3:00  A park — only if it happens; otherwise say this]**

If it’s vague it’ll show you two or three candidates and wait. That’s a park,
not a failure. Answer like a human: “the ISM one, from surveys.” Never “just
pick the first” — those 0.8 scores are often the wrong sibling.

**[3:20  Iterate — talk through 4–5 of these, don’t rush]**

No “edit” button. Every ask is a fresh render. Claude remembers the last spec
and changes the one thing you named.

Title:
> Title it “Factory output is lagging the survey”.

Legend:
> Change the second legend to “Philly Fed current activity”.

Dual axis:
> Put the Philly Fed line on the right axis with its own scale.

Transform:
> Drop the z-score and plot the raw index.
  — or —
> Make it year-over-year percent change instead of the level.

Recessions / lag / bars, pick one:
> Add recession bands.
> Lag the survey by three months.
> Make the second series bars instead of a line.

X-axis:
> Start it at 2010. Label the x-axis by year.

**[5:30  Check the last value]**

> The source chart’s last reading for the survey line is about 2.4.
> Does yours match?

If it doesn’t, something is bound or transformed wrong. A wrong chart still
looks like a chart.

**[6:00  File, if you want it]**

> Give me the link to this chart so I can save the file.

That’s the `/chart/…` URL. The picture in the chat is for looking; the link
is for putting it in a doc.

**[6:20  Close]**

So: connector once, paste a screenshot, then keep talking. Metadata finds
tickers, data gives you the numbers, this one draws the figure. Use whichever
door you need.

If a transform comes back refused, read the error — it lists the wordings it
accepts. Restate it. Don’t work around it. That’s how we stopped a level
quietly plotting as a percent change.
