---
title: Device Connect Driver
emoji: 📡
colorFrom: slate
colorTo: indigo
sdk: static
pinned: false
short_description: Run the Device Connect/MHP Reachy Mini driver with a LAN settings UI.
tags:
  - reachy_mini
  - reachy_mini_python_app
---

## Device Connect Edge Driver

Thin [Hugging Face Space](https://huggingface.co/docs/hub/spaces) package wrapping the
**reachy-mini-driver** package (Driver repository: link your own GitHub `reachy-mini-driver` fork in this README when you publish).

### Generate `pyproject.toml` (required)

`pyproject.toml` is **generated** by `sync_driver_dependency.py` so `reachy-mini-driver` is referenced with a URL pip can resolve (bare `file:..` breaks in the assistant’s temp venv).

**Local monorepo / `reachy-mini-app-assistant check`:**

```bash
python hf_space/sync_driver_dependency.py local
reachy-mini-app-assistant check hf_space
```

**Reset committed placeholder** (invalid until you set GitHub username):

```bash
python hf_space/sync_driver_dependency.py template
```

**Before `reachy-mini-app-assistant publish`** (HF uploads only this folder—use Git or PyPI):

```bash
python hf_space/sync_driver_dependency.py git --url 'git+https://github.com/<you>/reachy-mini-driver.git@main'
# or: python hf_space/sync_driver_dependency.py pypi --version 0.1.0

reachy-mini-app-assistant publish hf_space
```

### Community marketplace

Uses the standard Reachy flow: [Make and publish your Reachy Mini App](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps).

