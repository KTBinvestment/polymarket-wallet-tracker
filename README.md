# Polymarket Wallet Tracker — Etap 2

Bezpieczna wersja research: obserwuje portfele, filtruje prawdopodobne rynki sportowe, robi prosty ranking portfeli i pozwala pobrać CSV.

Nie handluje, nie używa kluczy prywatnych i nie łączy się z Twoim portfelem.

## Uruchomienie

```powershell
cd "B:\POLYMARKET BOT\polymarket_wallet_tracker_v1\polymarket_wallet_tracker"
..\.venv\Scripts\python.exe -m streamlit run app.py
```

Jeżeli tworzysz nowe środowisko:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\activate
cd polymarket_wallet_tracker
..\.venv\Scripts\python.exe -m pip install -r requirements.txt
..\.venv\Scripts\python.exe -m streamlit run app.py
```

## Co jest nowe w Etapie 2

- filtrowanie prawdopodobnych rynków sportowych,
- ranking portfeli,
- największe ruchy,
- szacunkowy notional = price × size,
- zapis snapshotów do folderu `data`,
- pobieranie CSV.

## Co dalej

Etap 3: symulacja kopiowania po 1s/2s/5s i ocena, czy po ruchu lidera zostaje edge.
