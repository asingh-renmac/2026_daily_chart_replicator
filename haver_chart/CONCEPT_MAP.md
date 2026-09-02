# Concept map — networking, identity, and MCP

Paste **§1 (the prompt)** and **§2–§4** into another chat. That agent should teach
**one topic at a time**, in plain language, until you say you are solid.

This file is only a **curriculum of technical ideas**. It does not describe a
specific codebase and does not ask anyone to open local files.

---

## 1. Prompt — paste this into the teaching agent

```
You are a patient tutor. I want a deep, intuitive grasp of the computer-science
ideas listed in the document I pasted: how chat apps call outside programs, how
a laptop process talks vs how a public URL talks, how DNS and tunnels work, and
how sign-in (OAuth / Entra) sits in front of a hosted service.

I am not asking you to operate any server, open any repository, or look up any
project files. Teach the concepts only. Do not invent or request file paths,
repo names, or product-specific URLs.

How to teach
- Follow the numbered teaching order in section 2. ONE topic per turn.
- Start with a one-sentence real-life picture (post office, hotel key, phone
  book) if it stays accurate.
- Then explain the idea in simple steps. Define every jargon word the first
  time you use it. Do not assume I know DNS, OAuth, JWT, stdio, or “the cloud.”
- Then ask 2–3 short check questions. Wait for my answers before the next topic.
- If I say I already know it, skip ahead. If I am shaky, stay on the topic and
  try a second analogy — do not just repeat the first paragraph.
- Prefer layman’s terms. About 400–700 words per topic unless I ask for more.
- When two terms are easy to mix up (section 4), contrast them before moving on.
- Do not dump the whole glossary. Do not write a textbook chapter.
- Architecture and intuition only. No secrets, tokens, or attack steps.

When I say “next,” go to the next topic. When I say “map,” show where we are
and what later topics depend on. When I say “quiz me,” test only the last
three topics.

Begin at topic 1 after you confirm you have the document.
```

---

## 2. Teaching order (the concept map)

Read top to bottom. Later topics assume earlier ones. Skip a whole **layer**
only if you already live in that world.

```
Layer A — What kind of system this is
  1  Client vs server
  2  API and “tool”
  3  MCP (Model Context Protocol)
  4  FastMCP
  5  Transport (same program, two pipes)

Layer B — The laptop path (no public internet)
  6  Process and child process
  7  stdin / stdout (stdio)
  8  JSON-RPC
  9  Python virtual environment (venv)
 10  Environment variables and .env files

Layer C — The remote path (a public name on the internet)
 11  IP address vs hostname
 12  DNS and DNS zone
 13  Subdomain
 14  Port
 15  Loopback (127.0.0.1)
 16  HTTP vs HTTPS / TLS
 17  Reverse proxy vs tunnel
 18  Cloudflare and cloudflared
 19  Named tunnel, public hostname, CNAME
 20  Firewall / NSG
 21  Streamable HTTP
 22  Health check
 23  Capability URL

Layer D — Who is allowed in
 24  Authentication vs authorization
 25  Identity provider (Microsoft Entra ID)
 26  Tenant, user, app registration, enterprise application
 27  OAuth (authorization code, redirect URI, PKCE)
 28  Dynamic Client Registration (DCR) and an OAuth proxy
 29  JWT and a signing key
 30  Bearer token and HTTP 401
 31  Scope and assignment-required
 32  Interactive sign-in vs client credentials (a robot)

Layer E — Machines that run the program
 33  Windows vs Linux (why some software cannot move)
 34  Virtual machine
 35  Virtual desktop (AVD) vs a dedicated VM
 36  Cloud VPS / droplet
 37  Keeping a program running (service managers)
 38  Remote desktop and a graphical login that can block a service

Layer F — Data stores and search (generic)
 39  Snapshot catalog vs live source-of-truth
 40  ETL
 41  Postgres and a hosted Postgres (pooled vs direct, cold start)
 42  Full-text search

Layer G — Safety habits that show up in servers
 43  Drawing a chart to a PNG (matplotlib)
 44  Lock, queue, process-wide vs machine-wide
 45  Path traversal
 46  UUID
 47  Fail-loud vs fail-soft
 48  Disambiguation: bind vs park (ask a human)
```

**What sits on what (mental picture, no product names):**

```
Chat app
  ├── stdio: starts a local program; talk over stdin/stdout
  └── HTTP: calls a public HTTPS name
            → DNS → edge / tunnel
            → loopback port on a host that must not be opened to the world
            → optional identity (browser sign-in) before tools run
```

---

## 3. Glossary (concepts only)

Short definitions so the tutor stays accurate. Expand these in conversation;
do not treat this list as the lesson.

### Layer A

**1. Client vs server**
A client asks; a server answers. A chat app is a client. The program that
exposes tools is a server. A browser fetching a file is also a client.

**2. API and “tool”**
An API is a fixed menu of requests. A **tool** is one item on that menu: a
name, arguments, and a result. The model is supposed to call the tool rather
than invent the answer the tool would have returned.

**3. MCP (Model Context Protocol)**
A standard for a chat app to call outside programs (search, fetch, draw)
instead of only generating text.

**4. FastMCP**
A Python library that turns ordinary functions into an MCP server, including
helpers for HTTP and for Microsoft sign-in.

**5. Transport**
The pipe the same tools travel over. **stdio** = the chat app starts the
program on that computer. **HTTP** = the program is already running; the chat
app calls a URL.

---

### Layer B

**6. Process / child process**
A running program. The chat app can **spawn** the server as a child. Closing
a window is not always the same as quitting the parent process, so an old
config can keep running.

**7. stdin / stdout (stdio)**
The child’s input and output streams. MCP messages are text on that pipe.
Nothing is published on the internet. Extra prints on stdout can corrupt the
protocol.

**8. JSON-RPC**
A small “please run this method with these arguments” format, written as JSON.
That is the language on the stdio pipe.

**9. Virtual environment (venv)**
A private Python plus libraries so one project’s packages do not fight
another’s. The interpreter that *runs* the server is the one that must have
the libraries.

**10. Environment variables and `.env`**
Settings the process reads from the OS (URLs, secrets, feature flags). A
`.env` file is a local list of those settings, usually not committed to git.

---

### Layer C

**11. IP address vs hostname**
An IP is the machine’s number. A hostname is a name people remember. DNS
turns the name into “where to send the traffic.”

**12. DNS and DNS zone**
The internet’s phone book. A **zone** is one domain you control. Records in
that zone say what the apex and each subdomain point at.

**13. Subdomain**
The left-hand label (`chart.`, `data.`, or the bare domain). Different
subdomains can reach different programs on different machines.

**14. Port**
A numbered door on one machine. Two programs on one host use two ports
(for example 8100 and 8101). Public HTTPS is usually port 443.

**15. Loopback (127.0.0.1)**
“This computer, talking to itself.” A server can listen only here so the
public internet never hits that port directly. A tunnel or reverse proxy on
the same machine forwards inward.

**16. HTTP vs HTTPS / TLS**
HTTP is the web’s request language. HTTPS is HTTP plus TLS: encrypted path
and a certificate for the name. Browsers talk HTTPS to the public edge. The
hop from the local tunnel process to loopback can stay plain HTTP because it
never leaves the machine.

**17. Reverse proxy vs tunnel**
A **reverse proxy** sits on a machine that already has a public address and
forwards `443` → a local port. A **tunnel** is an *outbound* pipe from a
machine that should **not** open inbound ports. Neither one is a file store;
they carry bytes.

**18. Cloudflare and cloudflared**
Cloudflare is an edge network (and DNS host). **cloudflared** is the small
program on your machine that keeps a tunnel up to that edge.

**19. Named tunnel, public hostname, CNAME**
A **named** tunnel is a durable Cloudflare object with routes. A **quick**
tunnel is a throwaway URL. A **CNAME** is a DNS record that points a
hostname at the tunnel. One tunnel can publish more than one hostname.

**20. Firewall / NSG**
A list of which inbound ports the world may hit. A cloud **network security
group** should stay closed on the app ports if a tunnel is the only intended
front door. Opening those ports would bypass the tunnel (and usually the
sign-in).

**21. Streamable HTTP**
MCP carried over HTTP instead of stdio. A “custom connector” in a hosted
chat app uses this pipe, not a local child process.

**22. Health check**
A tiny unauthenticated `GET` that answers “is the process up?” A richer
health check can also say “is work in flight / when did work last finish?”
so a stuck job is not mistaken for a healthy idle server. It should not
poke the thing that might be stuck.

**23. Capability URL**
A link whose long random id *is* the secret (like a share link). Anyone who
has the URL can fetch the resource; there is no extra login. The file still
lives on the origin machine, not at the CDN, unless you chose to store it
there.

---

### Layer D

**24. Authentication vs authorization**
Authentication: “are you who you claim?” Authorization: “are you allowed to
do this?” Sign-in is the first. An assignment list or a permission scope is
the second.

**25. Microsoft Entra ID**
The organization’s sign-in system (formerly Azure AD). “Log in with
Microsoft.”

**26. Tenant / user / app registration / enterprise application**
**Tenant** = one organization’s directory. **App registration** = “this
program exists” (ids, secret, redirect URLs). **Enterprise application** =
the tenant’s copy where you assign **people**. Those two Azure screens are
easy to mix up.

**27. OAuth, authorization code, redirect URI, PKCE**
OAuth: “this app may act for me without getting my password.” The browser
goes to the identity provider, you sign in, the provider sends you back to a
**redirect URI** the app registered. **PKCE** makes a stolen redirect harder
to abuse. Hosted chat connectors typically require this interactive style.

**28. DCR and the OAuth proxy**
**Dynamic Client Registration**: the chat app wants to *create* an OAuth
client on the fly. Many enterprise identity systems do **not** offer DCR. An
**OAuth proxy** (for example FastMCP’s Azure helper) speaks DCR toward the
chat app and a normal registered app toward Entra. After that dance, the
resource server often checks a token **the proxy issued**, not a raw Entra
token you minted yourself.

**29. JWT and signing key**
A JWT is a signed blob that says “this session is good until …”. The
**signing key** must stay the same across restarts, or every reboot forces
everyone through the browser again.

**30. Bearer token and 401**
`Authorization: Bearer <token>` is how the client proves the session.
Unauthenticated access to a protected route should return **401**. That
means the door is locked, which is the correct result for a stranger.

**31. Scope and assignment-required**
A **scope** is a named permission on the token (for example `read`).
**Assignment required** means only listed people (or a group) may sign in,
unless an admin turns that off for the whole tenant.

**32. Interactive sign-in vs client credentials**
People use a browser. A **daemon** uses `client_id` + secret (or a
certificate), no human. That is a different OAuth grant. A server that only
implements the browser/proxy path will reject a robot’s Entra token until
someone adds that second grant.

---

### Layer E

**33. Windows vs Linux**
Some libraries and desktop engines exist only on one operating system. If
the work must call that engine, the server has to run on that OS. Work that
only reads a database can live on the other OS.

**34. Virtual machine**
A computer rented as software: your own OS, disk, and network rules.

**35. Virtual desktop (AVD) vs a dedicated VM**
A **virtual desktop** is built for a person logging in; idle policies and
pooled hosts can sign out or wipe the machine. A **dedicated VM** is built
to stay on and run services. Same apps can be installed on both; reliability
differs.

**36. Cloud VPS / droplet**
A small always-on Linux (or other) VM from a cloud vendor. Typical pattern:
a process manager, a reverse proxy, and automatic HTTPS certificates.

**37. Service managers (NSSM, Windows service, systemd)**
Ways to start the program at boot and restart it if it dies, without a
human leaving a terminal open.

**38. Remote desktop and a blocking GUI**
**RDP** (or similar) is a picture of the desktop. Some licensed desktop
software pops a **login window** when a credential expires. On a machine
with nobody watching, that call can **hang** rather than return an error.
A health check that never opens that window can still tell “busy” from
“stuck.”

---

### Layer F

**39. Snapshot catalog vs live source-of-truth**
A **catalog** is a copy of names and metadata from the last refresh. It can
be weeks behind. The **live system** is what is true *now*. Do not treat a
catalog “last date” as the latest real observation.

**40. ETL**
Extract-transform-load: a job that copies and reshapes data from a source
into a database other programs can query.

**41. Postgres and hosted Postgres**
**Postgres** is a relational database. A hosted offering may give a
**direct** endpoint (writes, admin) and a **pooled** endpoint (many short
reads). Some hosts scale compute to **zero** when idle; the first query
after that is slow (cold start) and can hit a statement timeout.

**42. Full-text search**
Search by words inside the database (ranked text match), as opposed to
exact id lookup or optional vector/semantic search.

---

### Layer G

**43. Matplotlib / PNG**
A Python library that draws a figure and writes an image file. The chat app
can show the image inline; a hosted path can also return a link.

**44. Lock, queue, process-wide vs machine-wide**
A **lock** lets only one piece of work run at a time inside **that
process**; extras **queue**. Two processes on one machine each have their
own lock unless they share one on purpose. Process-wide is not
machine-wide.

**45. Path traversal**
A request that tries to walk out of the allowed folder (`../` and friends).
A file-serving route should only open ids that map to known files, never a
raw user path.

**46. UUID**
A long random identifier. Used so URLs and filenames are not guessable
(`chart-01.png` would be).

**47. Fail-loud vs fail-soft**
**Fail-loud**: raise an error rather than guess. **Fail-soft**: keep going
with a default. When a wrong picture is worse than no picture, fail-loud is
the rule. Hosted servers may hide accidental stack traces while still
passing through intentional error messages.

**48. Bind vs park**
**Bind**: we are sure enough to proceed. **Park**: we are not; show options
and ask a person. Treating “top search score” as a bind is how near-twins
get swapped.

---

## 4. Easy mix-ups (teach these as pairs)

| Pair | Why they get mixed |
|---|---|
| Reverse proxy vs tunnel vs “the edge stores my files” | Pipe vs storage |
| App registration vs enterprise application | App credentials vs **who may sign in** |
| Hostname vs the process on a port | Name in DNS vs program listening locally |
| Catalog snapshot vs live data | Last copy vs source of truth now |
| stdio plugin vs hosted HTTP connector | Child process vs HTTPS + sign-in |
| Scope vs assignment list | What the token allows vs who can get a token |
| Process lock vs two programs on one machine | Queue inside one process vs two processes |
| Authentication vs a public health URL | Locked tools vs a pulse anyone can hit |
| Proxy-issued JWT vs a raw identity-provider token | What the API actually checks after the proxy |
| Interactive OAuth vs client credentials | Person in a browser vs a daemon |
