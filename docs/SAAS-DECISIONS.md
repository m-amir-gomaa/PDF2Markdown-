# Document Studio decisions — 14 October 2026

Latest user instructions supersede earlier drafts:

- Keep Document Studio as a hosted portfolio product with usable MinerU extraction.
- Amir's Modal account supplies all compute. Customer Modal-account creation and credential collection are discarded.
- A bounded very small-document trial precedes payment.
- Amir clarified the product is specifically faithful PDF-to-Markdown conversion for books and technical material, exemplified by the Linguavell preparation books. Do not expand it into a study platform, chatbot, flashcard generator or generic AI workspace. Downstream study/content processing belongs in the user's chosen tools.
- Customers pay as they go for processing, plus $1/month for access, explicitly confirmed by Amir. This is not the previously assumed $1 subscription with included compute. The user intends the service fee as profit, but the actual contribution is reduced by fees and operating costs.
- Billing stays disabled until the separately funded processing ledger and a real seller/product are implemented and verified. Fee cadence is resolved; subscription entitlement alone cannot authorize unpaid GPU work.
- Original product code, source datasets and uncommitted work were backed up/preserved before changes. Current staging contains protected trial, persistent job/quota ledger and source-aware exports. The old subscription verifier remains tested draft code, not the selected commerce implementation.

The current differentiated deliverable is a review-ready PDF-to-notes archive: original source PDF, Markdown/images, PDF-page navigation when upstream metadata supports it, source-map metadata and a report of detectable replacement glyphs/missing images. These flags do not certify linguistic accuracy, OCR fidelity, page correspondence or layout. No claims of unique features or superior extraction without a comparative benchmark.

Confirmed user price objective does not resolve actual profit after processor/payout fees, hosting, support, refunds, acquisition and tax. Payments are not live. No customer charge or ad network was activated. A public preview may run a limited trial only.

Deployment verification: private cloud MinerU conversion succeeded on an original one-page PDF, with readable Markdown and source-page metadata. Initial public startup failed because modal_fn.py was absent from the web image; the file was explicitly included and redeployed. Public /health and /api/session returned HTTP 200, health confirmed billing_enabled=false, and the former /upload route returned 404. A real public HTTP upload subsequently completed and downloaded a 2,295-byte source-aware archive; retrying the same request returned the same job and another session received 404 for status/download. Evidence is kept privately under ignored artifacts/public-verification.json and artifacts/public-verification-result.zip. This original synthetic document does not establish accuracy for academic or medical material, nor browser acceptance. Pricing and discovery hypotheses are separated in PRICING-AND-DISCOVERY.md.
