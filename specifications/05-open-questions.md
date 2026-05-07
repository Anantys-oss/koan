# 05 — Open questions

Everything Alexis needs to answer (or commit a "punt to v2") before Sprint 1 closes. Each question has a recommendation where I have a strong opinion; flagged `Strong recommendation`, `Lean`, or `Genuinely open`.

When you answer, please put a `**Decision:** …` line under each block. That becomes the source of truth and we delete the recommendation/lean afterward.

---

## A. Strategy & scope

### Q1 — Is "Koan Cloud" the official product name?
The brief, the RFC and issue #2 all use it. Lock it in or pick now. (I'd default to Koan Cloud.)

**Decision (Alexis):** No, we don't have a marketing name for now. But Koan Cloud is a perfect "codename" for the MVP. We'll spend time and energy on markting once the MVP is functional.

### Q2 — Marketing & dashboard domain layout
The brief says `koan.cloud` is the official entreprise site. Issue #2 hosts the dashboard on `anantys.com`. Three reasonable layouts:

- (A) `koan.cloud` = marketing only; dashboard at `anantys.com/koan` (issue #2 default).
- (B) `koan.cloud` = both marketing and dashboard, dashboard at `app.koan.cloud`. Anantys is invisible to the user.
- (C) `koan.cloud` redirects to `anantys.com/koan` for everything — single brand surface.

`Strong recommendation: (B) eventually, (A) for v1.` Reason: brand integrity matters for an entreprise developer tool, but rebuilding the auth/billing/mail surface under a fresh domain costs us 2+ weeks we don't have. Start under Anantys; cut over to `app.koan.cloud` post-PMF.

**Decision (Alexis):** Of course we will have a 100% dedicated domain name for that product. I see things this way: 
- Anantys is the company name (eg: like "Alphabet" or "Microsoft") - it will remain this way.
- our (first) SaaS product (currently advertised on `anantys.com` is actually named "Anantys Invest")
- the backend stack has many re-usable parts (Stripe subs plugged to our Anantys bank account, emailing service (replaces perfectly mailchimp-like APIs), Railway infra super scalable, etc). We want to decouple as much as we can the "Anantys Stack" from the "Anantys Invest" app code. Ideally, moving step by step towards a modularization of our stack so we could write and deploy as many SaaS products as we want in the futrue. In an ideal world, the current Ananatys backend monolith could become a "SaaS Cloud Building Toolkit" for our internal use. 
- `koan.cloud` can be the temporary domain of the project in these specs.
- We want to end up with a dedicated site : `koan.cloud` -> marketing/CTA/subscription site (like greptile.com) -> `koan.cloud/dashboard` -> customer dashboard, the app.

### Q3 — Free tier or trial?
- (A) No free tier, no trial. Pay $89 from day 1.
- (B) 14-day free trial of the Starter tier (CC required).
- (C) Free forever tier (1 project, capped tokens, scheduled active hours).

`Lean: (B) — 14-day trial, card required.` Why: Railway has a per-tenant cost floor (~$5–15/mo); free-forever bleeds money fast. 14 days lets a developer evaluate without commitment.

**Decision (Alexis):** No free tier. B2B. Maybe a 14-day free trial before payment via the native Stripe "free trial days" feature.

### Q4 — Target launch date for design-partner soft launch?
The roadmap says ~10 weeks. Is that aggressive enough, too aggressive, or about right? Is there an external deadline (conference, fundraise, demo)?
*Genuinely open.*

**Decision (Alexis):** We use intesively Claude Code + koan. We are **very** fast. We may have a first customer wanting to sponsor our development for this project. 
Ideally we want that SaaS to be MVP-functional by the end of the summer (in 3-4 months tops). If possible, before summer (2-3 months).

In this train of thought; what could be a good rationale to "sell" to our customer that they could "pay us to develop it"? I mean, "you are a VIP customer", "you can impact the roadmap from day 1", etc. Also: what good pricing could we demand? 10K, 50K ? For what? Help us on that. 

### Q5 — Pricing tiers: confirm the numbers
Brief says $89 / $249 / $499. RFC said $49 / $99 / $199. **Big delta.** What pricing analysis informed the brief's numbers, and is it locked or still moveable?

The higher the price, the more we can promise (more compute, more tokens, support). $89 entry ≈ $1k+ ARPU/year — feels right for an autonomous engineering agent, but only if features match. Need plan-feature mapping per tier.

`Lean: keep $89/$249/$499 as floor.` Sub-question: what *exactly* does each tier include? See Q18.

**Decision (Alexis):** this was purely arbitrary sample prices. Look first at greptile.com -> they do a very similar product but "only" for code reviews. Koan can do much more. Let's align ourselves to their pricing model as a reference, and expand from koan's features and capabilities.

---

## B. Anthropic & token model

### Q6 — Hosted credits vs BYOK at launch?
**This is the single biggest decision.**

- (A) BYOK: customer provides their Anthropic key. We charge for compute + product only.
- (B) Hosted credits: we hold the master key. Tier price includes $X tokens; overage billed via Stripe metered.

The brief says *"we should switch to a real Anthropic API key instead of a personal Claude token"* — which I read as "Anantys holds an Anthropic API key" (Model B), not "the customer brings one." But it's ambiguous.

`Strong recommendation: (B) — hosted credits.` Why: the click-and-play promise is broken if step 4 of onboarding is "go create an Anthropic account first." We absorb AUP enforcement complexity in exchange for a 5-minute onboarding.

**Decision (Alexis):** yes, it is a strong decision, we want th ecustomer to not even know about Anthropic! We are the product, and it works out of the box. So yes, We (Anantys) get an Anthropic API key and we use it for all our instances. The customer does not know about that. This implies a super fine-graiend monitoring of the tokens consumed, perfect alignment of prices on Opus costs, etc. The user will for sure compare our prices to a $200 Max license or Claude Code. I guess, a "clic and play" version of Koan, able to produce as many tokens as the $200 license of Claude Code should cost at least $500 (more than double the price of the tokens -> because full autonomy of the agent, persistent memory, Github chat, etc). A good starting point could be to use Anthropic's model and apply a factor there. 
Note that greptile has a cost per seat per month + limits on the number of reviews done. We could have : 
- limits on the number of missions taken 
- limits on the number of repositories plugged into koan 
- limits on the number of specific core skills used 
- limits on the number of recurring tasks scheduled 


### Q7 — Token allowance per tier (only if hosted credits)
What's "included" in each tier? Crude starting point:
- Starter $89 → ~$30/mo of Claude tokens included.
- Pro $249 → ~$100 included.
- Scale $499 → ~$220 included.

Margins look comfortable but are placeholders. Need a real consumption baseline from the Phase 0 POC + 1 month of dogfooding. `Genuinely open` — answer with "approximate, validate in Phase 0."

**Decision (Alexis):** see my previous answer, I think the safest approach is to align to Anthropci's price grid with a multiplier factor for margin. + sane limits and features. 

### Q8 — Overage handling
On token overage past the included allowance:
- (A) Hard cap: instance auto-pauses, customer must upgrade or top up.
- (B) Soft cap: keep running, customer billed metered overage at the end of the month.
- (C) Hybrid: customer-set monthly cap; under cap = metered, over cap = pause.

`Lean: (C) hybrid` — uses existing `pause_manager` and gives customer control. Default cap = "tier allowance + 50%."

**Decision (Alexis):** A : instance auto-pauses. Customer must upgrade in MVP. In MVP+X: they could "buy more credits".

### Q9 — Annual pricing?
Discount for annual upfront (e.g., 2 months free)? Doubles ACV but commits us to keeping a tenant alive 12 months. *Genuinely open.*

**Decision (Alexis):** yes, apply the Anantys rule that workds fine: ~30% discount for a yearly billing.
---

## C. Anantys reuse & architecture

### Q10 — Control plane location: monolith blueprint vs separate service?
- (A) Extend `anantys-back` with a `koancloud` blueprint (this is what doc 02 assumes).
- (B) New microservice `anantys-koan-cloud-back/` that reaches into Anantys via internal API for auth/mail/Stripe.

`Strong recommendation: (A).` Reason: Anantys monolith is already the system of record for users, billing, mail. Splitting now creates a distributed-systems tax we don't need at this scale.

**Decision (Alexis):** I'm a bit worried about continuing to expand the monolith with a completly different scope. I prefer to create a dedicated repo for koan.cloud, and add new API endpoing in Anantys under a new blueprint/prefix : `anantys-cloud` or `anantys-os` or `anantys-stack` (you can find a better name maybe). It seems way better and future-proof this way.

### Q11 — Dashboard: new Next.js app vs route group inside an existing one?
- (A) Brand new Next.js app at `anantys-back/src/koan-cloud-app/` (separate submodule).
- (B) New route group inside `corp-anantys` (sales site) at `/koan/*`.
- (C) New route group inside `investmindr-nextapp` (investment app).

`Lean: (A) new app.` Reasons: Greptile-inspired dark UI is a different aesthetic than Anantys investment app; isolating the bundle keeps the dashboard fast and the codebase clean. (B) is a fallback if we want to ship faster.

**Decision (Alexis):** yes, A : we now have a super experience with NextJS, it provides out of the box state of the art UIUX feel. Also, we should definitely re-use the design system of the `investmindr-nextapp` repo.

### Q12 — Auth: GitHub OAuth as a new Anantys provider, or only on the Koan signup path?
Today Anantys has Google + Apple OAuth + native email/password. Adding GitHub:
- (A) Universal: GitHub login becomes available everywhere on Anantys (investment app too).
- (B) Koan-only: GitHub OAuth is reachable only via the Koan signup flow.

`Lean: (A).` Adding it universally is cleaner than carving out a "Koan-only" auth path; it's the same code either way, and there's no harm in offering it elsewhere.

**Decision (Alexis):** I think this could be a dedicated code in the koan cloud dashboard repo: GitHub makes no sense for Anantys Invest. Maybe the koan.cloud code should have their own DB + User base? 

### Q13 — Existing Anantys user signing up for Koan: do they need to GitHub-OAuth too?
Yes, almost certainly — even if we have the Anantys session, we still need a GitHub token to list and act on repos. Confirm.

**Decision (Alexis):** they are "invest" users -> absolutely nothing to do with koan. Should be completely different universes, I think.

### Q14 — Session sharing across `koan.cloud` and `anantys.com`
If dashboard lives at `anantys.com/koan/*`, session cookies just work. If it ever moves to `app.koan.cloud`, we need cross-domain SSO (token-issued or shared parent domain). Plan for this seam.
*Genuinely open — defer to v2 cutover.*

**Decision (Alexis):** no sharing of sesssions: different products entirely.

### Q15 — Mail sending domain
Mix Koan emails with `@anantys.com` sender, or use `noreply@koan.anantys.com` (or `noreply@koan.cloud`)?
`Lean: noreply@koan.anantys.com` — preserves Anantys reputation while branding emails as Koan.

**Decision (Alexis):** in an ideal world, we send emails with the product's domain. See if Mailgun allows us to use multiple domains from the same API key.

### Q16 — WebSocket reuse: shared infra or separate process?
- (A) Reuse the existing `RAILWAY_TARGET=websocket` server, add a `/koan/chat` namespace.
- (B) New WS process `RAILWAY_TARGET=koan-websocket`.

`Lean: (A).` Operational simplicity wins until we hit scale.

**Decision (Alexis):** yes, it works very well, and I would like to avoid rewriting it. Maybe the chatbot component in the anantys investmindr-nextapp should be duplicated as well, or re-used?
---

## D. Tenant runtime & Railway

### Q17 — Confirm: 1 Railway service per customer at all scales?
At 100 paying customers = 100 Railway services. RFC says yes. Any concern about Railway pricing/limits at that count? Should we also de-risk a Fly.io alternative path?
`Lean: confirm 1-per-customer, plan a Fly.io spike at 50 paying customers.`

**Decision (Alexis):**  good point. We may picked a bad hypothesis here. Advise for a better model where a single "koan worker" instance can process many koan cloud instances, while keeping **very good isolation of context**. Your ideas are worht reading.


### Q18 — Plan-to-resource mapping
What does each tier give the customer beyond price?

| Tier | Compute | Token allowance | Active hours | Projects | Concurrency | Support |
|---|---|---|---|---|---|---|
| Starter $89 | ? | ? | ? | 1 | 1 mission at a time | community |
| Pro $249 | ? | ? | 24/7 | 3 | 1 | email |
| Scale $499 | ? | ? | 24/7 | 10 | 1 | priority |

Fill the `?` cells. *Genuinely open.*

**Decision (Alexis):**  we keep that open for now. 

### Q19 — Active hours feature for Starter?
The RFC mentions running Koan only N hours/day for cheap tiers. Worth the engineering, or push everyone to 24/7?
`Lean: skip in Epic #1`, all tiers run 24/7.

**Decision (Alexis):**  I think the whole point is having koan 24/7 and limited by the plan used only. Settings allow the user to define heavy/light hours and pause mode, etc. 

### Q20 — Railway region default + EU residency
Default region (US? EU?). Any EU customer commitment we want to make on day 1?
*Genuinely open.*

**Decision (Alexis):** MVP is EU only.

### Q21 — Update strategy: per-instance auto-update vs control-plane redeploy?
Doc 03 recommends control-plane-driven. Confirm.
`Strong recommendation: control-plane redeploy for cloud, per-instance auto-update only for self-hosted.`

**Decision (Alexis):**  very good question. I don't know :) @Nicolas ?

### Q22 — Backup cadence and retention
Daily snapshot of `instance/`? 7 days? 30? 90?
`Lean: daily snapshots, 14-day retention, lifelong retention of the most recent on tenant destroy (30 days then deleted).`

**Decision (Alexis):** good question, maybe we should flag that "all .md files" in the koan infra should be mirrored to a DB for koan Cloud? I don't know. Here also, you should suggest best design decisions.

### Q23 — What happens on payment failure?
Card declines. Workflow:
- (A) 3-day grace, mail reminder day 1 and day 3, then suspend.
- (B) Immediate suspend, mail with reactivation link.

`Lean: (A) 3-day grace.` Standard.

**Decision (Alexis):** A.

### Q24 — Cancellation grace
Customer cancels. Their tenant lives for N days then is destroyed.
`Lean: 7 days, snapshot retained 30.`

**Decision (Alexis):** just shut down the koan at the last day of billing IMHO.
---

## E. GitHub integration

### Q25 — GitHub OAuth vs GitHub App at launch?
`Lean: OAuth + user token at launch, GitHub App in v2.` Confirm.

**Decision (Alexis):** GitHub OAuth for fetching the user's scope (projects to work on). In MVP yes, the user has to input its GitHub token. In later vrsion ,we buld a native GitHub app to provide an out of the box "koan" bot available to any GitHub customer.

### Q26 — How many repos per tier?
Already partially in Q18. Confirm.

**Decision (Alexis):** to be defined later. Just make it configurable from day 1 per tier.

### Q27 — Required GitHub scopes
At minimum: `repo` (private read+write), `workflow` (CI), `read:user`, `user:email`. Anything else?
*Genuinely open.*

**Decision (Alexis):** see koan docs, it should be mirrored.

### Q28 — What if a repo has GitHub Actions failing? CI cost?
Koan triggers CI when it pushes a branch. If the repo's CI is broken or expensive, Koan can rack up GitHub Actions minutes for the customer. We need to make this visible in the dashboard (and perhaps default branch protection).
*Open — flag for Phase 2.*

**Decision (Alexis):** I think it is not koan's job to think about that, at least, for now.

---

## F. Web dashboard

### Q29 — Confirm Greptile-inspired dark UI
Issue #2 already commits to this. Confirm or open a design exploration.
`Lean: confirmed.`

**Decision (Alexis):** yes. but with Anantys Design System.

### Q30 — Chat experience: in scope for Epic #1?
The brief says "yes, the dashboard replaces Telegram with a chat experience." That implies real-time chat is critical-path. Issue #2 lists it. Confirm chat is in v1.
`Lean: yes, chat is in v1`, otherwise we don't replace Telegram and the value prop slips.

**Decision (Alexis):** Maybe we could postpone the Chatbot in MVP+1, indeed.

### Q31 — Slash commands: parsed where?
- (A) Client-side (`/mission ...` in the chat input → POST `/missions`).
- (B) Server-side (chat handler routes commands).
- (C) Both — client highlights, server validates.

`Lean: (B)` — keeps the dashboard dumb, command_handlers logic untouched.

**Decision (Alexis):** MVP is github only, probably. So we're speaking about guthub mentions only, for MVP.

### Q32 — Live logs: SSE or WebSocket?
Both work. WS gives bidirectional flexibility we won't use; SSE is simpler. The chat already needs WS.
`Lean: SSE for logs, WS for chat. Two channels, two simple jobs.`

**Decision (Alexis):**  postponed question after MVP.

### Q33 — Mobile responsive vs full mobile UX?
Issue #2 says responsive only. Confirm.
`Lean: confirmed, defer mobile-polish to v2.`

**Decision (Alexis):** not a priority.

---

## G. Operations

### Q34 — Who is on-call once we go live?
Both? Alexis weekdays, Nicolas weekends? Rotation?
*Genuinely open.*

### Q35 — Initial SLAs we commit to
Internal target vs customer-facing commitment. Suggest:
- Internal: 99.5% uptime, 1h response on Slack-class incidents.
- Customer-facing: "best-effort during business hours" for Starter; "24/7 monitoring" for Scale.
`Lean: that split.`

**Decision (Alexis):**  not a subject for these specs.

### Q36 — Status page?
`status.koan.anantys.com` (or `status.koan.cloud`)? `Lean: yes — Statuspage / BetterStack from week 1, even before launch.`

**Decision (Alexis):**  not MVP.

### Q37 — Where do issues live?
`koan-private` for tenant-side work, `anantys-back` for control-plane work, dashboard in either. Shared milestone "Koan Cloud Epic #1" in both.
`Lean: as written.`

**Decision (Alexis):** koan-private, the OSS koan code is koan only, not the SaaS version.

### Q38 — Sprint cadence
1-week or 2-week sprints?
`Lean: 1-week.` Two-week sprints rot when the team is two engineers.

**Decision (Alexis):** 1 week.
---

## H. Things to think about that aren't questions

These don't have clean yes/no answers but should not be forgotten:

- **Customer-perceived first-mission magic.** The welcome mission is the customer's first impression of Koan. Invest disproportionately in making it *good* — not "hello world" but "scanned your repo, here's what I noticed, here are 3 missions worth running first." **Decision (Alexis):**  yes vry good idea to have a exec summary of the repo with most important findinfs as a hello world mission.
- **What happens when Koan opens a bad PR on a customer's repo?** Reputation risk. Default to `auto_merge: false`, draft PRs, very clear warning that the human still owns the merge. **Decision (Alexis):**  koan is already quite good at that.
- **Observability.** From day 1: per-tenant log aggregation, error reporting (Sentry?), tenant health dashboard for ops. This belongs in Phase 5 of the roadmap but absolutely cannot be skipped.
- **Cost monitoring.** A tenant whose Anthropic spend is 10× the median needs to be flagged automatically. Build the alert before we have such a tenant.
- **Kōan agent prompts may need to know they're in cloud mode.** E.g., "you are running on a customer's repos in a managed environment, follow these conservative defaults." Worth a system prompt review pass.
