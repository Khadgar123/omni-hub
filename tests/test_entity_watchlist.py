"""Entity-watchlist <-> follow-dispatcher contract (v0.49 broad acquisition).

Guards the invariant that every source key used in
``config/entity-watchlist.yaml`` is dispatchable by
``scripts/follow_entity.py``, that the broad AI sources (X / GitHub / HF Hub
/ 小红书 / 公众号) are wired, and that the watchlist parses + is well-formed.
Pure config/structure — no network.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _load_follow_entity():
    spec = importlib.util.spec_from_file_location(
        "follow_entity_under_test", _ROOT / "scripts" / "follow_entity.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _valid_domains() -> set[str]:
    from omni_hub import domain_schemas as ds

    schemas = getattr(ds, "DOMAIN_SCHEMAS", {})
    out: set[str] = set()
    if isinstance(schemas, dict):
        out |= {str(k).replace("-", "_") for k in schemas}
        items = list(schemas.values())
    else:
        items = list(schemas)
    for s in items:
        slug = getattr(s, "slug", None)
        if slug:
            out.add(str(slug).replace("-", "_"))
    return out


class EntityWatchlistContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import yaml

        cls.fe = _load_follow_entity()
        with (_ROOT / "config" / "entity-watchlist.yaml").open(encoding="utf-8") as f:
            cls.wl = yaml.safe_load(f)
        cls.entities: dict[str, dict] = {}
        for bucket in ("people", "companies", "institutions", "topics"):
            for k, v in (cls.wl.get(bucket) or {}).items():
                v = dict(v)
                v["_bucket"] = bucket
                cls.entities[k] = v

    def test_yaml_loads_and_has_buckets(self) -> None:
        for bucket in ("people", "companies", "institutions", "topics"):
            self.assertIn(bucket, self.wl)
        self.assertGreaterEqual(len(self.entities), 30)

    def test_every_source_key_is_dispatchable(self) -> None:
        valid_keys = {cfg for (cfg, _b) in self.fe._SOURCE_DISPATCH.values()}
        for eid, ent in self.entities.items():
            for key in (ent.get("sources") or {}):
                self.assertIn(
                    key, valid_keys,
                    f"entity {eid!r} uses undispatchable source key {key!r}",
                )

    def test_broad_ai_sources_registered(self) -> None:
        for s in ("x", "github", "hf_hub", "xhs", "wechat"):
            self.assertIn(s, self.fe._SOURCE_DISPATCH)

    def test_entities_well_formed(self) -> None:
        bucket_kind = {
            "people": "person", "companies": "company",
            "institutions": "institution", "topics": "topic",
        }
        for eid, ent in self.entities.items():
            self.assertTrue(ent.get("display"), f"{eid} missing display")
            self.assertEqual(
                ent.get("kind"), bucket_kind[ent["_bucket"]],
                f"{eid} kind/bucket mismatch",
            )
            self.assertTrue(ent.get("primary_domain"), f"{eid} missing primary_domain")

    def test_primary_domain_is_a_known_domain(self) -> None:
        valid = _valid_domains()
        self.assertTrue(valid, "could not resolve DOMAIN_SCHEMAS slugs")
        for eid, ent in self.entities.items():
            dom = str(ent.get("primary_domain", "")).replace("-", "_")
            self.assertIn(dom, valid, f"{eid} primary_domain {dom!r} unknown")

    def test_ai_labs_present(self) -> None:
        # institutions used to be gov-only (scotus/fed); v0.49 adds AI labs.
        for lab in ("fair", "ai2", "msr", "eleuther"):
            self.assertIn(lab, self.entities)
            self.assertEqual(self.entities[lab]["primary_domain"], "ai_progress")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
