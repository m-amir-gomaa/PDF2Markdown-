# PDF2Markdown Web

A serverless, GPU-accelerated PDF extraction web application.

This project wraps the powerful [MinerU](https://github.com/opendatalab/MinerU) extraction pipeline in a modern FastAPI backend with an HTMX + Alpine.js frontend. It accurately converts complex PDFs (including tables, math, and images) into clean Markdown.

## Features

- **Serverless Architecture:** The web app and GPU extraction functions are fully deployed on [Modal](https://modal.com/).
- **GPU Acceleration:** Uses an A10G GPU only when processing, scaling to zero when idle.
- **Modern UI:** Built with Tailwind CSS, HTMX for seamless SSE streaming, and Alpine.js for drag-and-drop interactions.
- **Clean Output:** Automatically filters out debug artifacts, returning a clean ZIP file containing only the Markdown, images, and `content_list.json`.

## Deployment

Deploy the entire stack with a single command:

```bash
modal deploy app.py
```

## Tech Stack

- **Backend:** Python, FastAPI, Modal
- **Frontend:** HTML, Tailwind CSS, HTMX, Alpine.js
- **AI/ML:** MinerU Pipeline, PyTorch

## License

MIT License
