"""Public index-roster smoke; never reads holdings or Telegram data."""
from portfolio_doctor_peers import official_nifty500_industries

def main():
    roster = official_nifty500_industries()
    print("MSK_DOCTOR_NIFTY500_PUBLIC_ROSTER members={} verified={}".format(
        len(roster), len(roster) >= 450))
    # Informational provider health check: the report itself fails closed.
if __name__ == "__main__":
    main()
