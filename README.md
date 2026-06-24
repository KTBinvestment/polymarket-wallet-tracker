# Polymarket Wallet Tracker - Etap 4

Bezpieczna aplikacja research i live paper trading dla publicznej aktywnosci portfeli Polymarket.

Aplikacja dalej nie handluje realnie, nie uzywa kluczy prywatnych i nie laczy sie z Twoim portfelem.

## Bezpieczenstwo

- aplikacja nie sklada realnych zlecen,
- nie wymaga klucza prywatnego,
- przyszla granica wykonawcza w `execution_gateway.py` jest celowo wylaczona,
- paper trading zapisuje dane lokalnie w `data/paper_trading.db`,
- awaryjny STOP blokuje nowe pozycje paper i zatrzymuje worker.

## Szybki start na Windows

Najprosciej:

```powershell
cd "sciezka\do\polymarket-wallet-tracker"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run_app.ps1
```

Skrypt utworzy lokalne `.venv`, zainstaluje `requirements.txt` i uruchomi dashboard Streamlit.

Nastepnie otworz `http://localhost:8501`.

## Manualne uruchomienie

```powershell
cd "B:\POLYMARKET BOT\polymarket-wallet-tracker"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

Jesli `py -3.12` nie dziala, uzyj:

```powershell
python -m venv .venv
```

## Jak uzywac

1. Wklej adresy portfeli w panelu bocznym albo uzyj obecnej listy `wallets.txt`.
2. Kliknij `Test API`, zeby sprawdzic polaczenie z publicznym Data API Polymarket.
3. Kliknij `Pobierz pelna swieza migawke`, zeby pobrac transakcje, otwarte pozycje i zamkniete pozycje.
4. Analizuj ranking portfeli, symulator kopiowania i money management.
5. W sekcji paper trading uruchom worker tylko wtedy, gdy komputer moze zostac wlaczony.

Zwykle odswiezenie strony korzysta z zapisanej migawki. Nowe dane pobierasz dopiero przyciskiem odswiezania.

## Migawka danych

Przycisk `Pobierz pelna swieza migawke` pobiera dla kazdego portfela:

- paginowana historie transakcji,
- aktualne otwarte pozycje,
- zamkniete pozycje wraz z realized P/L.

Pliki sa zapisywane atomowo, wiec nieudane pobranie nie niszczy poprzedniej migawki.

## Live paper trading

Paper worker:

1. obserwuje nowe publiczne transakcje liderow,
2. czeka 1s, 2s albo 5s,
3. pobiera rzeczywisty orderbook,
4. przechodzi po poziomach plynnosci,
5. uwzglednia spread, poslizg, czesciowe wykonanie i fee curve,
6. stosuje limity ryzyka,
7. zapisuje decyzje i pozycje do SQLite,
8. subskrybuje publiczny WebSocket orderbooka dla monitorowanych pozycji,
9. zamyka papierowe pozycje, gdy lider ma pozniejszy SELL na tym samym tokenie.

Komputer musi pozostac wlaczony. Do wiarygodnej oceny potrzebne sa co najmniej 2-4 tygodnie nieprzerwanego paper tradingu.

## Testy

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Realny trading

Nie jest zaimplementowany. Przed jego dodaniem wymagane sa:

- pozytywny i stabilny wynik paper tradingu,
- kontrola geoblock dla aktualnego miejsca operatora,
- osobny portfel z malym limitem kapitalu,
- bezpieczne przechowywanie klucza i API credentials poza repozytorium,
- audyt logiki zlecen, anulowania, heartbeat i awaryjnego zatrzymania.
