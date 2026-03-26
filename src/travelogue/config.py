"""Trip configuration: load and validate trip.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class PersonConfig(BaseModel):
    id: str
    display_name: str
    source_label: str
    attribution_mode: str = "minimal"


class PhotoInputConfig(BaseModel):
    path: str
    person_id: str


class InputsConfig(BaseModel):
    photos: list[PhotoInputConfig] = Field(default_factory=list)
    journals: list[str] = Field(default_factory=list)


class AiProviderConfig(BaseModel):
    model: str = "gemini-3-flash-preview"


class AiTasksConfig(BaseModel):
    summarize_days: bool = True
    summarize_events: bool = True
    label_subjects: bool = True
    draft_missing_journal_text: bool = False


class AiConfig(BaseModel):
    default_provider: str = "gemini"
    providers: dict[str, AiProviderConfig] = Field(default_factory=dict)
    tasks: AiTasksConfig = Field(default_factory=AiTasksConfig)


class RenderConfig(BaseModel):
    theme: str = "editorial"
    default_view: str = "everything"
    show_maps: bool = True
    attribution: str = "minimal"


class DeployConfig(BaseModel):
    target: str = "local"
    private: bool = True
    bucket: str = ""
    distribution_id: str = ""


class TripMeta(BaseModel):
    id: str
    title: str
    timezone: str = "UTC"
    description: str = ""


class TripConfig(BaseModel):
    trip: TripMeta
    people: list[PersonConfig] = Field(default_factory=list)
    inputs: InputsConfig = Field(default_factory=InputsConfig)
    ai: AiConfig = Field(default_factory=AiConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)

    @classmethod
    def load(cls, path: Path) -> "TripConfig":
        with open(path) as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return cls.model_validate(data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False, sort_keys=False)


TRIP_YAML_TEMPLATE = """\
trip:
  id: {trip_id}
  title: {title}
  timezone: UTC
  description: ""

people:
  - id: p1
    display_name: Me
    source_label: my_phone
    attribution_mode: minimal

inputs:
  photos:
    - path: imports/originals/my_phone
      person_id: p1
  journals:
    - journals/

ai:
  default_provider: gemini
  providers:
    gemini:
      model: gemini-3-flash-preview
  tasks:
    summarize_days: true
    summarize_events: true
    label_subjects: true
    draft_missing_journal_text: false

render:
  theme: editorial
  default_view: everything
  show_maps: true
  attribution: minimal

deploy:
  target: local
  private: true
  bucket: ""
  distribution_id: ""
"""
