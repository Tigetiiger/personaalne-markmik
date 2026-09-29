# Kontode ühendamine

Kõik välised kontod ühendatakse rakenduses endas: **Seaded → Ühendused**. Pärast esimest
sisselogimist juhatab viisard „Alustame“ samad sammud läbi samas järjekorras. Iga sammu võib
vahele jätta ja hiljem Seadetes lõpetada.

Iga kaardi juures on olek (Ühendatud / Pole ühendatud / Viga koos viimase veaga), juhised,
väljad ning nupud **Testi** ja **Katkesta**. Paroolid ja tokenid hoitakse krüpteeritult
(`MARKMIK_KEY`); brauser näeb neist ainult viimast nelja märki (`••••1234`).

`.env` failis on ainult see, mida rakendus käivitumiseks vajab: `SECRET_KEY`, `OWNER_PASSWORD_HASH`,
`MARKMIK_KEY` ja `CLAUDE_CODE_OAUTH_TOKEN`. Vaata `.env.example`. Omaniku parooli räsi teeb
`uv run python -m app.hashpw` (küsib parooli kaks korda ega näita seda ekraanil); pane see
`.env` faili ühekordsetesse jutumärkidesse, `OWNER_PASSWORD_HASH='…'`, muidu rikub docker compose
selle `$`-märgid ära.

## 1. Claude

1. Kui token on juba `.env` failis (`CLAUDE_CODE_OAUTH_TOKEN`), pole vaja midagi teha: vajuta
   **Testi**.
2. Uue tokeni saamiseks ava VM-is terminal ja käivita `claude setup-token`.
3. Logi avanenud lehel oma Claude'i kontoga sisse ja kinnita.
4. Kopeeri terminali ilmunud token (algab `sk-ant-`), kleebi see kaardile ja vajuta **Salvesta**.
5. **Testi** teeb ühe väikese päringu ja näitab mudelit ning vastamise aega.

Rakenduse AI kasutab sinu Claude'i tellimust. Selle kasutuspiirangud on ühised sinu enda Claude
Code'i kasutusega.

## 2. eKool

1. Sisesta sama kasutajanimi ja parool, millega logid sisse aadressil <https://ekool.eu>.
2. Vajuta **Salvesta**. Parool hoitakse krüpteeritult ja seda ei näidata enam kunagi.
3. **Testi** logib eKooli sisse ning näitab õpilase nime ja eelolevate kodutööde ja
   kontrolltööde arvu.

eKool pole avalikku API-t õpilastele. Rakendus kasutab veebilehe enda päringuid, mis võivad ilma
hoiatuseta muutuda, ja seda pole veel päris kontoga proovitud. Kui **Testi** annab vea, vaata
`docs/spikes/ekool.md`. Kahefaktorilise sisselogimisega kontot pole proovitud.

## 3. Google'i kontod (Gmail)

### Üks kord: OAuth-klient

1. Ava [Google Cloud Console](https://console.cloud.google.com/projectcreate) ja loo uus projekt
   (nt „Märkmik“).
2. **APIs & Services → Library**: otsi **Gmail API** ja vajuta **Enable**.
3. **OAuth consent screen**: kasutajatüüp **External**; lisa ulatus `…/auth/gmail.readonly` ja
   mõlemad Google'i kontod testkasutajateks.
4. Vajuta **Publish app → In production**. Muidu aegub ühendus 7 päevaga.
5. **Credentials → Create credentials → OAuth client ID**, rakenduse tüüp **Desktop app**.
   Laadi JSON-fail alla.
6. Laadi see fail kaardil üles (või kleebi selle sisu).

### Iga konto jaoks

1. Vajuta **Lisa Google'i konto**: uues vahekaardis avaneb Google'i leht.
2. Vali konto ja luba ligipääs. Kui Google hoiatab „Google hasn't verified this app“, vajuta
   **Advanced → Go to … (unsafe)**: see on sinu enda rakendus.
3. Lõpuks jõuab brauser aadressile, mis algab `http://127.0.0.1`, ja leht ei avane. Nii peabki
   olema.
4. Kopeeri kogu aadress aadressiribalt, kleebi see kaardi kasti ja vajuta **Ühenda konto**.
5. Teise konto jaoks korda samme.

Kui kooli konto ei luba ühendust (koolid võivad alaealistel kinnitamata rakendused keelata),
suuna kooli kirjad Gmaili seadetes isiklikule aadressile.

Rakendus loeb ainult kirju, mis vastavad Seadetes valitud sildile või saatjate nimekirjale.
Kindlad sündmused lisatakse kalendrisse „AI“ märgiga; ebakindlad jäävad **Postkasti**.

## 4. Brain repo

Märkmed salvestatakse Markdown-failidena repo `Tigetiiger/brain` kausta `notes/`.

1. Ava GitHubis
   [Settings → Developer settings → Fine-grained tokens → Generate new token](https://github.com/settings/personal-access-tokens/new).
2. **Repository access**: vali **Only select repositories** ja sealt `Tigetiiger/brain`.
3. **Permissions → Contents**: **Read and write**.
4. Vajuta **Generate token**, kopeeri token (algab `github_pat_`), kleebi see kaardile ja vajuta
   **Salvesta**.
5. **Testi** kontrollib ligipääsu (`git ls-remote`), kloonib repo ja näitab viimast commit'i.

Rakendus kirjutab ainult kausta `notes/`. Märkme fail uueneb 2–7 minutit pärast viimast muudatust.
Käsupaletis (Ctrl+K) saadab käsk **Sünkrooni märkmed brain'iga** muudatused kohe.

## 5. ntfy teavitused

1. Serveri aadress on juba täidetud (VM-i ntfy server `http://100.122.97.7:8090`).
2. Mõtle välja teema nimi, mida keegi ära ei arva (nt `markmik-` ja paar juhuslikku sõna), ja
   telli sama teema telefoni ntfy rakenduses.
3. Tokenit on vaja ainult siis, kui server nõuab sisselogimist.
4. Vajuta **Salvesta** ja siis **Testi**: telefoni tuleb proovisõnum.

## Turuandmed (Aktsiad)

Aktsiate hinnad tulevad Yahoost ilma võtmeta. Võtmed lisavad USA reaalajas hinnad, uudised ja
börsiteated. Seda kaarti esmakäivituse viisard ei näita: ava **Seaded → Ühendused**.

1. **Finnhub**: loo tasuta konto aadressil <https://finnhub.io/register> ja kopeeri **Dashboard**ilt
   API võti.
2. **Alpha Vantage** (valikuline): tasuta võti aadressilt
   <https://www.alphavantage.co/support/#api-key>. Sellega saab 25 uudisepäringut päevas.
3. **SEC User-Agent**: USA börsijärelevalve nõuab kontaktandmeid, nt `Markmik nimi@example.com`.
4. Vajuta **Salvesta** ja siis **Testi**: see teeb ühe päringu igasse allikasse, millel on võti
   (Alpha Vantage'i päevapiiri ei kulutata).

Aktsiate teated lähevad samale ntfy serverile teemale, mis on kirjas **Seaded → Aktsiad** (vaikimisi
`stock-oracle`). Telli see teema telefoni ntfy rakenduses.

## 6. Kalendrivood (ICS)

1. Kopeeri kalendri ICS-aadress: eKooli kooliürituste voog, Moodle või mõni muu kalender. See
   algab tavaliselt `https://` või `webcal://`.
2. Kleebi see kaardile, anna voole nimi ja vajuta **Lisa**. Sama moodi saad lisada mitu voogu.
3. **Testi** näitab, mitu sündmust leiti.

## 7. Telefoni kalender

1. Vajuta kaardil **Kopeeri**.
2. iPhone: **Seaded → Kalender → Kontod → Lisa konto → Muu → Lisa tellitud kalender** ja kleebi
   aadress.
3. Android: paigalda rakendus **ICSx⁵** ja lisa seal uus tellimus selle aadressiga.

Aadress on salajane: kes seda teab, näeb su kalendrit. Ära jaga seda. Aadressi ei saa praegu
vahetada.

Google'i kalender uuendab tellitud kalendreid ainult 12–24 tunni tagant. Telefonis on kiirem
kasutada rakendust ennast (lisa see avaekraanile) või ICSx⁵-i.

## Hiljem: avalikud jagamislingid

Praegu töötavad jagamislingid ainult Tailscale'i võrgus. Avalikuks tegemiseks on vaja Cloudflare
Tunneli tokenit ja hostinime. Enne seda tuleb seadistada `HTTPS_ONLY=1` ja see, et rakendus näeks
tunneli taga õiget kliendi aadressi (vaata `docs/SECURITY-REVIEW.md`).
