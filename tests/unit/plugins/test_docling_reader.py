from plugins.document_sources.docling_reader import normalize_timeline


def test_normalize_timeline_pairs_event_with_following_month_year() -> None:
    text = """
CRONOLOGIA DELLA CAMPAGNA DI INFORMAZIONE
Le BCN spediscono pubblicazioni sulla nuova banconota da euro 20.
maggio
2015
Opuscoli sul nuovo biglietto da euro 20 sono inviati a tre milioni di punti di vendita.
ottobre
2015
"""

    normalized = normalize_timeline(text)

    assert "maggio 2015: CRONOLOGIA" in normalized
    assert (
        "ottobre 2015: Opuscoli sul nuovo biglietto da euro 20 sono inviati "
        "a tre milioni di punti di vendita."
    ) in normalized
