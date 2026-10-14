# Pricing and discovery — 14 October 2026

Confirmed user objective: customers fund processing separately and pay $1/month for access. Cadence is resolved. The user intends this as profit, but a $1 price does not establish $1 net profit. No pricing is activated by this analysis.

## Unit economics

A fee is revenue, not net profit. For a separate $1 monthly subscription payment, Lemon Squeezy's base 5% + $0.50 plus 0.5% subscription fee leaves $0.445; a 1% non-US bank payout leaves $0.44055. This excludes international/PayPal surcharges, sales tax effects on processing fees, refunds, support, infrastructure, acquisition and income taxes. Provider approval is not established; this is a fee illustration, not an activated checkout.

If $12/year were offered, the same simplified fee schedule would leave $10.7316/year after the example payout fee, approximately $0.8943/month. Annual prepayment reduces fixed checkout overhead but must offer genuinely useful continuing access; it is a recommendation, not the user's approved billing cadence. Processing-credit top-ups incur their own fees and must fund compute plus attributable fees, failed-work treatment and any disclosed reserve. A monthly subscription must not authorize unfunded compute.

| Paying subscribers | Monthly access revenue | After example subscription and bank-payout fees |
|---|---:|---:|
| 100 | $100 | $44.06 |
| 500 | $500 | $220.28 |
| 1,000 | $1,000 | $440.55 |
| 5,000 | $5,000 | $2,202.75 |
| 10,000 | $10,000 | $4,405.50 |

Counts are scenarios, not forecasts. Figures are contribution before remaining costs, not net profit. Approximately 2,270 paying subscribers would be needed to produce $1,000 before other costs under these assumptions. An assumed six-month customer lifetime leaves only $2.64 before support/hosting, limiting affordable acquisition spending. No actual conversion, retention or customer-lifetime data exists for Document Studio yet. Budget on $0 receipts until actual purchases establish revenue.

[Lemon Squeezy fees](https://docs.lemonsqueezy.com/help/getting-started/fees): base fee 5% + $0.50; additional international, PayPal and subscription fees may apply; fee calculated on total order including tax. Non-US bank payout fee 1%. Custom pricing is offered for products below $10. Seller approval and negotiated fees are not established.

Candidate: $5–$10 minimum processing-credit purchases to amortize fixed payment costs. This is a recommendation, not an approved wallet design. Must define refunds, failure handling and processing budget limits before implementation. Never describe prepaid credit funding as earned profit before service delivery. Measure cold starts, GPU duration, CPU, storage and failed jobs before announcing a MinerU usage tariff.

## Audience and competition

Latest scope correction: Amir wants PDF-to-Markdown, not a study platform. Primary audience hypothesis: people who repeatedly need faithful editable text from books and technical PDFs for their own tools—independent researchers, advanced self-learners, graduate/STEM students, educators and developers building private source collections. Medical students qualify when they actually need Markdown exports; PDF volume alone does not establish that demand. No chatbot, generated study notes, flashcard generator or learning dashboard is proposed. NotebookLM is an adjacent downstream tool, not the direct conversion benchmark.

Technical/medical study PDFs can expose important failures: numerals, negations, units, tables, equations, symbols and figures must be preserved and reviewable against source pages. Exported images do not establish correct diagram interpretation. Source links do not prove extracted text is correct. These requirements need representative-document tests before accuracy claims. Private Linguavell extraction evidence exposes a concrete case: 300 replacement glyphs in the 221-page Hall pronunciation book; the current SaaS engine is now separately pinned and was tested on a synthetic one-page document, not that whole book. Do not extrapolate a perfect book-conversion result from it.

Sources retrieved 14 October 2026:

- [Mathpix Snip pricing](https://mathpix.com/pricing/snip): advertises $4.99/month including 1,000 PDF pages plus image allowance; free tier also available. Compare customers' total access-plus-usage spend, not merely the $1 headline. No comparative extraction benchmark was performed.
- [MinerU's official hosted platform](https://mineru.net/): online use, document parsing, structured export and integrations advertised. Self-hosting our selected engine is not a unique customer benefit.
- [Google's free and paid document-notebook plans](https://support.google.com/gemininotebook/answer/16213268?hl=en) and [source-based flashcards/quizzes](https://blog.google/innovation-and-ai/models-and-research/google-labs/notebooklm-app-quizzes-flashcards/): substantial alternative for document-based study. Plain AI summarization does not establish differentiation.
- [LlamaParse plans](https://www.llamaindex.ai/pricing): developer-facing hosted extraction with a free tier and paid plans.
- [Mathpix PDF-to-Markdown](https://mathpix.com/pdf-to-markdown), [Marker](https://github.com/datalab-to/marker) and [Docling](https://docling-project.github.io/docling/): existing technical-document conversion, image/structure handling and Markdown output. These are direct comparison candidates; export formats/source metadata/LLM compatibility alone cannot be presented as unique. Current provider descriptions are not independent measured benchmarks or licenses for our pinned engine.

Assessment: crowded converter/document-AI category; saturation of our specific workflow is not measured. Exact active users of MinerU-on-Modal cannot be inferred from repository stars or search results. Architecture choice is useful infrastructure, not proof of product demand. No feature novelty, search-volume, market-share or revenue forecast is claimed.

Recommended validation, not an already completed study: recruit 20 qualified users through opt-in university/research communities without paid ads; collect 10–20 permission-cleared representative PDFs; compare current export against existing tools; measure correction time, fidelity of tables/equations/images and source navigation; ask for actual paid use at fully disclosed $1/month plus processing. First commercial checkpoint: 5 paying users and at least 3 returning for a second month, not just one free conversion. These are decision thresholds, not statistically conclusive evidence or promises. Cap discretionary validation compute spend before running the larger cohort; no extra budget is authorized by this document.

The candidate utility specification and acceptance priorities are in CONVERSION-PRODUCT.md. They are not all implemented features. The existing public trial is one page; the retired monthly draft's 50-page limit is not an approved full-book commercial limit.

## Search discovery

Build original useful public guides and real before/after exports for narrow needs such as PDF-to-Obsidian with source-page navigation, research notes and difficult document review. [Google's helpful-content guidance](https://developers.google.com/search/docs/fundamentals/creating-helpful-content) supports original useful content; it does not promise rankings or purchases. Treat query choices as hypotheses, not researched search-volume claims.

Track landing visits → trial attempts → successful exports → checkout → completed paid conversions → repeat use. Hypothetical 1,000 targeted visits × 1% purchase rate = 10 buyers. Neither figure is a forecast. Measure willingness to pay and job contribution separately from traffic.

## Advertising

Optional later experiment on original public guides after publisher approval. Keep paid/private document workspace free of ads. Do not add ads to empty processing/error/navigation screens: [Google publisher policy](https://support.google.com/publisherpolicies/answer/11112688?hl=en). No ad network is currently connected.

[Google Page RPM](https://support.google.com/adsense/answer/112030?hl=en): revenue = page views / 1,000 × page RPM. At an assumed $5 RPM, 10,000 views yields $50 and 100,000 yields $500. $5 is an illustration, not an expected rate; approval, geography, topic, fill, ad blockers and consent affect revenue. Ad revenue must not justify unlimited free GPU conversion.
