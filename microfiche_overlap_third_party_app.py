#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import List

import microfiche_overlap_extractor as core


APP_NAME = "Microfiche Problem Detector"


def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", str(Path.home())))
        root = base / "MicroficheOverlapExtractorThirdParty"
    else:
        root = Path.home() / ".microfiche_overlap_extractor_third_party"
    root.mkdir(parents=True, exist_ok=True)
    return root


class ThirdPartyStorage(core.Storage):
    def __init__(self) -> None:
        self.root = app_data_dir()
        self.models_path = self.root / "models_config.json"
        self.memory_path = self.root / "memory_store.json"
        self.last_scan_path = self.root / "last_scan.json"

    def load_models(self) -> List[core.ModelProfile]:
        defaults = self.default_models()
        defaults_by_name = {m.name: m for m in defaults}
        if not self.models_path.exists():
            self.save_models(defaults)
            return defaults
        try:
            raw = json.loads(self.models_path.read_text(encoding="utf-8"))
            out: List[core.ModelProfile] = []
            for x in raw:
                raw_name = str(x.get("name", "")).strip()
                raw_model = str(x.get("model", "")).strip()
                display_name = core.normalize_display_model_name(raw_name, raw_model)
                if display_name not in defaults_by_name:
                    continue
                tpl = defaults_by_name[display_name]
                out.append(
                    core.ModelProfile(
                        name=display_name,
                        base_url=str(x.get("base_url", tpl.base_url)),
                        model=display_name,
                        api_key=str(x.get("api_key", tpl.api_key)),
                        timeout_sec=int(x.get("timeout_sec", tpl.timeout_sec)),
                    )
                )
            seen = {m.name for m in out}
            for d in defaults:
                if d.name not in seen:
                    out.append(d)
            return out
        except Exception:
            return defaults

    def save_models(self, models: List[core.ModelProfile]) -> None:
        self.models_path.write_text(
            json.dumps([asdict(m) for m in models], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def default_models() -> List[core.ModelProfile]:
        return [
            core.ModelProfile(
                name="GPT-5.4",
                base_url="https://ai.last.ee",
                model="GPT-5.4",
                api_key="sk-9b06f0ac4851ba8cdef2498ba269978ae5c64e099720b2a1b32d0d1b5f6631b4",
                timeout_sec=120,
            ),
            core.ModelProfile(
                name="GPT-5.3-Codex",
                base_url="https://ai.last.ee",
                model="GPT-5.3-Codex",
                api_key="sk-9b06f0ac4851ba8cdef2498ba269978ae5c64e099720b2a1b32d0d1b5f6631b4",
                timeout_sec=120,
            ),
            core.ModelProfile(
                name="Claude-Opus-4.6",
                base_url="https://cursor.scihub.edu.kg/api/v1",
                model="Claude-Opus-4.6",
                api_key="cr_56c958bfb141949f0a7e3ce7bf9e83315fe7695edf95749683c05b234c594000",
                timeout_sec=150,
            ),
            core.ModelProfile(
                name="Kimi-K2.5",
                base_url="https://coding.dashscope.aliyuncs.com/v1",
                model="Kimi-K2.5",
                api_key="sk-sp-a745d056ce96479c899d2b5d9c40d345",
                timeout_sec=120,
            ),
        ]


def main() -> None:
    core.APP_NAME = APP_NAME
    core.Storage = ThirdPartyStorage
    app = core.App()
    app.title(APP_NAME)
    app.log("Detector started.")
    app.mainloop()


if __name__ == "__main__":
    main()
