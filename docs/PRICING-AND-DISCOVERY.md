# Pricing and first customers — 14 October 2026

Current offer: one-time $5/$10 consumable processing packs, no recurring access fee. Amir delegated a better pricing model; the former $1/month access proposal is historical and superseded. Payments are not yet activated. Technical details are in BOOK-RELEASE.md; operational evidence and account actions are in LAUNCH.md.

## Tariff and costs

MinerU: $0.50/job + $0.005/selected page (100 pages $1, 500 $3, 1,000 $5.50). Text-only: $0.25 + $0.001/page (500 pages $0.75), excluding OCR/images/layout reconstruction. Reprocessing completed pages: $0.20 + method page rate. Failed sections release customer allowance; owner compute for failed attempts still costs money.

At [Lemon Squeezy's published fees](https://docs.lemonsqueezy.com/help/getting-started/fees), a $5 payment with base 5% + $0.50, an assumed additional 1.5% international-card surcharge and 1% non-US bank payout leaves approximately $4.13325; $10 leaves $8.7615. These illustrations exclude tax effects, PayPal surcharges, refunds and other adjustments. Fees apply to tax-inclusive order totals; actual settlement can differ. No negotiated sub-$10 rate is assumed.

Prepaid money remains a service obligation until consumed or refunded. A fully consumed $10 pack would contribute $6.76 after an assumed $2 compute cost, or $2.76 after $6 compute, before other costs. Neither compute assumption is measured commercial evidence. Do not call a top-up profit on purchase. Cold starts, failures, free samples, support and user acquisition all matter. Cap and measure them.

Observed owner workspace snapshot before this operations release: Modal current-month metered usage $1.27927267 and billed cost $0 after adjustments, queried through installed SDK 1.5.3. It covers owner tests and all workspace work, not the marginal cost of a representative customer job. Current-cycle totals can change. Free credits explain cash affordability now but are not a sustainable unit-economics assumption. Refresh using the operations report.

[Modal pricing](https://modal.com/pricing) supplies the resource rates; [workspace budgets/spend limits](https://modal.com/docs/guide/budgets) distinguish total usage from net charges. Admission budgets in our ledger are separate estimates and cannot establish an account-level bill ceiling.

## What the income target would require

Treat this as conditional arithmetic, not a revenue prediction. Suppose $25/month other cash overhead and measured contribution per active purchasing customer of either $2 or $6, after variable processing/payment costs. Required monthly active buyers = ceiling((income target + overhead) / contribution). Acquisition/support must be included; unpaid owner labour and income tax still reduce take-home.

| Target monthly amount before income tax/owner labour | At $2 contribution/customer | At $6 contribution/customer |
|---|---:|---:|
| $300 | 163 | 55 |
| $1,000 | 513 | 171 |
| $3,000 | 1,513 | 505 |

The $6 case requires meaningful regular purchasing, not someone spending $1/month. A book-only converter can have intermittent rather than monthly use; do not relabel pack buyers subscribers or assume every buyer returns monthly. If real contribution is $0.50, $300 plus the assumed $25 overhead requires 650 active buyers. Existing admission caps would need deliberate funded expansion well before these traffic levels. Thousands of supported concurrent users are not established.

Budget on $0 sales until actual purchases. First decision: will five qualified users buy, do three return for more documents/top-ups, and does each completed batch contribute positively at actual measured costs? A useful portfolio deliverable can still exist if commercial demand is modest; commercial success must be judged separately.

## Audience and alternatives

Primary hypothesis: people already trying to move books/papers into editable Markdown for their own editor or LLM workflow—advanced self-learners, research students, educators and developers assembling private sources. Medical/engineering students qualify when they actually want this format; owning many PDFs alone does not establish willingness to pay. Focus on conversion, source inspection and portable output rather than study summaries/chat/flashcards.

Direct comparison candidates: [Mathpix PDF-to-Markdown](https://mathpix.com/pdf-to-markdown) / [Snip prices](https://mathpix.com/pricing/snip), [MinerU's hosted platform](https://mineru.net/), [Marker](https://github.com/datalab-to/marker), [Docling](https://docling-project.github.io/docling/). Mathpix advertises $4.99/month with 1,000 PDF pages; customers may prefer that recurring allowance at frequent volume. Source metadata/Markdown exports are not demonstrably unique. Convenience, pricing and correction time require a document-by-document comparison. Repository stars are not customer counts or demand evidence.

No study-platform expansion, paid ads or ad network is selected. The public guide and original downloadable PDF/Markdown/ZIP are implemented at /guides/pdf-to-markdown. They demonstrate a real text-layer conversion, not superior OCR or medical/scientific accuracy. Private Linguavell books are not marketing assets without publication rights.

## First-user procedure

1. Use your existing portfolio, public repository and appropriate opt-in Obsidian/research communities. Read each community's promotion rules; do not send bulk messages or pretend to be an unaffiliated user. No posts or emails were sent by this release.
2. Invite 10–20 people who already need Markdown. Ask what document they last converted, which tool they used, what was wrong and how much correction time it took. Seek permission before receiving/publishing excerpts. Use the bounded sample allowance, not unlimited free books.
3. Ask them to compare the same pages using their current workflow. Record source type, tables/symbols/reading-order faults, correction minutes, total price and whether the archive imports cleanly. Use benchmark-template.csv; synthetic success alone is insufficient.
4. After approved payments, ask for actual $5/$10 purchase at the displayed tariff. Record genuine purchases, completed service, refunds, support minutes and subsequent use. Do not infer willingness to pay from compliments or free usage.
5. Review weekly. Traffic without inspections: improve relevance/copy. Inspection without useful output: fix conversion/review. Useful output without purchases: investigate total price, alternatives and trust. Purchases without positive contribution: adjust tariffs/costs before increasing limits. Returning buyers with positive contribution justify broader distribution.

Suggested honest owner-post copy, not sent: “I built a PDF-to-Markdown converter for people who use their own editors/LLMs. You can select source pages, review against the PDF, save corrections and export a portable archive. Here is an original PDF and its actual export: [guide link]. I would like feedback from people already converting books/papers—especially where symbols, tables or reading order go wrong. A bounded one-page sample is available; paid checkout is not open yet.”

Track aggregate landing source counts → inspections → bounded trial starts → export requests, and separately authoritative commercial purchases/returning buyers. Counts include bots/reloads and are not unique-person conversion rates. No raw PDF content or payment keys belong in analytics. No third-party tracker is installed.

For Google: create an owner [Search Console URL-prefix property](https://support.google.com/webmasters/answer/9008080), obtain its real verification token, add the matching verification file/meta, then submit the implemented sitemap.xml and inspect the guide URL. Domain ownership of modal.run is not ours; use URL-prefix verification. A canonical tag/sitemap does not establish indexing or ranking. [Google Search Essentials](https://developers.google.com/search/docs/essentials) and [helpful-content guidance](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) support crawlable original useful pages, not guaranteed sales.

Optional ads are deferred until substantial useful public traffic and publisher approval. They would stay off private conversion/review/payment flows and must not fund unlimited free GPU use. A few hundred/thousand dollars monthly remains a goal whose feasibility depends on actual paid repeat demand and contribution.
