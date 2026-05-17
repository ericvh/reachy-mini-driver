---
title: Device Connect Driver
emoji: 📡
colorFrom: gray
colorTo: indigo
sdk: static
pinned: false
short_description: Reachy Mini Device Connect driver with portal settings UI
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
python device-connect-driver/sync_driver_dependency.py local
reachy-mini-app-assistant check device-connect-driver
```

**Reset committed placeholder** (invalid until you set GitHub username):

```bash
python device-connect-driver/sync_driver_dependency.py template
```

**Before `reachy-mini-app-assistant publish`** (HF uploads only this folder—use Git or PyPI):

```bash
python device-connect-driver/sync_driver_dependency.py git --url 'git+https://github.com/<you>/reachy-mini-driver.git@main'
# or: python device-connect-driver/sync_driver_dependency.py pypi --version 0.1.0

reachy-mini-app-assistant publish device-connect-driver
```

### Community marketplace

The folder name **`device-connect-driver`** is the Hugging Face Space slug (e.g. `ericvh/device-connect-driver` in the app marketplace—not `hf_space`). After renaming locally, create the Space on the Hub or rename the old one, then point the nested git remote at `https://huggingface.co/spaces/<user>/device-connect-driver` before publishing.

Uses the standard Reachy flow: [Make and publish your Reachy Mini App](https://huggingface.co/blog/pollen-robotics/make-and-publish-your-reachy-mini-apps).

