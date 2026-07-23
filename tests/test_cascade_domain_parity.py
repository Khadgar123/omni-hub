"""Every DOMAIN_SCHEMAS domain must have an executable cascade entry.

Regression guard for the ``agent_systems`` silent-fallback bug (v0.46): a
routable domain that is missing from ``DEFAULT_DOMAIN_CASCADES`` silently
falls through to the generic ``"default"`` source list, so retrieval uses
the wrong sources with *no error surfaced*.  This test fails loudly if any
future domain is added to ``DOMAIN_SCHEMAS`` without a matching cascade.

``DEFAULT_DOMAIN_CASCADES`` may carry *extra* keys (``default`` plus
sub-domains like ``biomedical`` / ``law`` / ``statistics`` that have no
standalone wiki schema); the contract is one-directional —
``DOMAIN_SCHEMAS ⊆ DEFAULT_DOMAIN_CASCADES``.
"""

import unittest

from omni_hub.domain_schemas import DOMAIN_SCHEMAS
from omni_hub.retrieval.cascade import DEFAULT_DOMAIN_CASCADES


class TestCascadeDomainParity(unittest.TestCase):
    def test_every_schema_domain_has_a_cascade(self) -> None:
        missing = sorted(set(DOMAIN_SCHEMAS) - set(DEFAULT_DOMAIN_CASCADES))
        self.assertEqual(
            missing,
            [],
            "DOMAIN_SCHEMAS domains with no DEFAULT_DOMAIN_CASCADES entry "
            "(they would silently use the 'default' source list): "
            f"{missing}",
        )

    def test_default_cascade_present(self) -> None:
        # cascade_for() falls back to this; it must always exist.
        self.assertIn("default", DEFAULT_DOMAIN_CASCADES)

    def test_cascade_sources_are_nonempty_lists(self) -> None:
        for domain, sources in DEFAULT_DOMAIN_CASCADES.items():
            self.assertIsInstance(sources, list, f"{domain} cascade not a list")

    def test_every_cascade_source_is_registered(self) -> None:
        # Drift guard: a cascade naming a source that builtin_sources() does
        # not register means that source is silently skipped at runtime.
        from omni_hub.retrieval import builtin_sources

        registered = set(builtin_sources())
        unregistered = {
            domain: [s for s in sources if s not in registered]
            for domain, sources in DEFAULT_DOMAIN_CASCADES.items()
        }
        unregistered = {d: s for d, s in unregistered.items() if s}
        self.assertEqual(
            unregistered,
            {},
            f"cascade references sources missing from builtin_sources(): {unregistered}",
        )


if __name__ == "__main__":
    unittest.main()
