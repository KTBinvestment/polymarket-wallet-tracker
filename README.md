# Polymarket Wallet Tracker - Etap 3

Bezpieczna aplikacja research do obserwowania portfeli Polymarket. Pobiera publiczne transakcje, filtruje prawdopodobne rynki sportowe, tworzy ranking walletow, szuka nowych kandydatow i symuluje, czy kopiowanie ruchow po 1s/2s/5s mialoby jeszcze sens cenowy.

Aplikacja nie handluje, nie uzywa kluczy prywatnych i nie laczy sie z Twoim portfelem.

## Szybki start na Windows

Najprosciej:

```powershell
cd "sciezka\do\polymarket-wallet-tracker"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\run_app.ps1
```

Skrypt utworzy lokalne `.venv`, zainstaluje `requirements.txt` i uruchomi dashboard Streamlit.

## Manualne uruchomienie

```powershell
cd "sciezka\do\polymarket-wallet-tracker"
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
3. Kliknij `Pobierz / odswiez dane z API`, zeby utworzyc lokalna migawke w `data/`.
4. Analizuj ranking portfeli, najwieksze ruchy, symulator kopiowania i money management.
5. Uzyj `Szukaj nowych walletow`, zeby przeskanowac publiczne transakcje i znalezc kandydatow.

Zwykle odswiezenie strony korzysta z zapisanej migawki. Nowe dane pobierasz dopiero przyciskiem `Pobierz / odswiez dane z API`.

## Co jest w Etapie 3

- pobieranie publicznych trade'ow portfeli z fallbackiem na activity,
- filtr sport/e-sport po tytule rynku,
- ranking obserwowanych portfeli,
- linki do profili Polymarket,
- wyszukiwarka nowych walletow z publicznych transakcji,
- symulacja kopiowania po 1s/2s/5s,
- money management: kapital, ryzyko na ruch, limit stawki i wirtualny P/L,
- eksport CSV dla danych filtrowanych, raw, symulatora i money management.

## Aktualny kierunek rozwoju

Najblizszy sensowny krok to Etap 4: ostrzejszy filtr kandydatow i dashboard decyzyjny, czyli mniej szumu, lepszy ranking, blacklisty rynkow, minimalna liczba prob, analiza profitow oraz czytelna lista walletow do obserwowania dalej.

## Bezpieczenstwo

To narzedzie research-only. Nie ma modulu skladania zlecen, nie przechowuje private key i nie powinno byc traktowane jako rekomendacja inwestycyjna.