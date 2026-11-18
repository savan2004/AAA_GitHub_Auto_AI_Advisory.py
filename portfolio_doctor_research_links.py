"""Optional deep-equity research links for a private one-time Doctor result.

Reuse the existing /report SYMBOL generator, which already creates MARICO/REFEX
style full company reports with a PDF release gate. Do NOT rebuild its PDF in
the Doctor worker, store holdings, or start five heavy jobs automatically.
"""
def full_research_choices(filename, payload):
    """Return display symbol / existing PDF callback pairs, not client holdings.

    Skip explicitly BSE-held symbols: the shared `pdf|SYMBOL` route currently
    resolves bare NSE names and must not silently analyse a different exchange.
    Parse a second time only after verified PDF delivery; never log/retain
    quantities, average costs, user IDs or raw upload.
    """
    from portfolio_doctor_live import PortfolioDoctorError, parse_holdings
    try:
        rows = parse_holdings(filename, payload)
    except PortfolioDoctorError:
        return ()
    return tuple((row["symbol"], "doctorpdf|" + row["symbol"])
                 for row in rows if row.get("exchange") != "BSE")
