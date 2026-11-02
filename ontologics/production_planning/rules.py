"""Reserved rule identifiers, not implementations or registered Agent tools."""

_VERSIONS = {
    'materials': 'materials-0.1',
    'procurement': 'procurement-0.1',
    'production': 'production-0.1',
}


def calculation_rule_version(domain: str) -> str:
    """Select explicitly; never use the installation-time rule as a fallback."""
    if not isinstance(domain, str) or domain not in _VERSIONS:
        raise ValueError('Use materials, procurement or production')
    return _VERSIONS[domain]
