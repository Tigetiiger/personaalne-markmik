# Hackathons and similar competitions for an Estonian 12th grader: sources

Checked on **2026-09-27**. Every claim below was checked on that date. "(search only)" means the fact came
from a search-engine snippet and not from the page itself. "UNVERIFIED" means it was not confirmed.

## Summary

- **The Teaduskool CSV does not cover hackathons, robotics or research competitions.** Its "Võistlused"
  group lists subject contests: Känguru, Náboj, Physics Cup, Kajakas, ASI Karikas, Kobras, e-rehkendus
  and the JA Eesti õpilasfirma final. Its "Rahvusvahelised" group lists BOI and IOAI. But the CSV has had
  no Robotex row since 2019, no teadustööde konkurss row since 2020 and no cyber row since 2022. It has
  never listed a hackathon, CanSat or Robolahing. Checked by downloading
  `https://tk-kalender-files.s3.eu-central-1.amazonaws.com/kalender.csv` and grepping it.
- **Hackathons in Estonia have no central calendar.** They are posted on organizer sites: Garage48,
  the regional business incubators (Kesk-Eesti, Pärnumaa), TalTech events, and youth or regional
  development centres. Most of them are listed nowhere else. The generic aggregators were empty or
  full of noise: Eventbrite, hackathon.com, Startup Estonia, and the Hack Club events API.
- **Five structured feeds were found and each one works:**
  1. Miks.ee "Õpilasele → Sündmused": RSS and WP JSON.
  2. robootika.ee (Haridusrobootika veebikodu): **iCal**, RSS and a JSON events API.
  3. TalTech events: **Drupal JSON:API** with date and title filters.
  4. ETAg event calendar: **iCal**.
  5. Kesk-Eesti Ettevõtlusinkubaator events: RSS and WP JSON.

  The following also work: Devpost JSON (online hackathons), CTFtime JSON (CTFs), Codeforces JSON and
  the ERR Novaator RSS (news). The Robotex International RSS works but is stale.
- **Pages with dates in HTML but no feed:** Garage48 /events, the JA Eesti /kalender (dd.mm. with no
  year), the EIO calendar, the KüberOlümpia Google Site, ecsc.ee, robotex.ee, the Junction site and
  kultuuriaken.tartu.ee.
- **Big change:** Robotex International 2025 (5–6 Dec 2025) was "the last time in Estonia". The Estonian
  youth event continues as **Robotex Eesti (11–12 Dec 2026, T1 Center)**.

### Upcoming items a 12th grader could still enter (as of 2026-09-27)

| When | What | Source |
|---|---|---|
| reg. by **30.09.2026**; prelim. 5–9.10, final 10.12.2026 | KüberNaaskel CTF (14–25 y), which selects the ECSC team | sites.google.com/view/kyberolympia/avaleht |
| **2–4.10.2026** | Pärnu CoastHack, age 16+, free | coasthack.ee/et/ |
| **9–11.10.2026** | TalTech Ehitusvaldkonna Häkaton (eligibility UNVERIFIED) | taltech.ee/sundmused/ehitusvaldkonna-hakaton |
| deadline **15.10.2026** | Õpilasleiutajate riiklik konkurss (6–20 y) | miks.ee/opilasele/sundmused/opilasleiutajate-riiklik-konkurss-2026/ |
| qual. **16.10**, final **31.10.2026** | AHHAA Robolahing, including 24 school heavyweight battles | robootika.ee iCal; kultuuriaken.tartu.ee |
| **16–18.10.2026** | Garage48 Future of Wood makeathon, Väimela | garage48.org/events |
| 1.10–6.11 prelim., final **25.11.2026** | KüberPuuring (grades 7–12) | KüberOlümpia site |
| apply by **25.10.2026**; event **13–15.11.2026** | Junction 2026, Espoo ("mainly 18 and over") | 2026.hackjunction.com |
| **14–15.11.2026** | NASA Space Apps Challenge (search only; no Estonian local site verified) | spaceappschallenge.org/2026/ |
| **11–12.12.2026** | Robotex Eesti youth day, T1 | robotex.ee |
| **16.01.2027** | Delta X 2027 (robotics, Tartu) | robootika.ee iCal |
| **24.04.2027** | FIRST Tech Challenge Estonia pilot, Kiili Gümnaasium | robootika.ee iCal |
| **6–7.05.2027** | Eesti parim Õpilasfirma ja minifirma 2027 | ja.ee/kalender |

---

## Sources to check every run

| Name | URL | What it lists | Feed type | Fit for a 12th grader | Update frequency | Notes |
|---|---|---|---|---|---|---|
| **Miks.ee – Õpilasele / Sündmused** (ETAg's science portal) | RSS `https://miks.ee/opilasele/sundmused/feed/`; JSON `https://miks.ee/wp-json/wp/v2/events?per_page=50` | Science and technology events for students: competitions (Õpilasleiutajate konkurss, Robolahing, teadustööde konkurss), camps, festivals, CanSat trainings | **RSS + WP REST JSON** | High. Many items are for teachers or younger pupils, so filter them. | Several posts a week (8 posts on 21–25 Sep 2026) | The `date` field is the post date. The **event date is only in the body text** and must be parsed. `event_cat` has only 2 values (17 = sündmus, 18 = näitus). Page with competitions: `https://miks.ee/opilasele/konkursid/` |
| **robootika.ee – Haridusrobootika veebikodu** | iCal `https://www.robootika.ee/?post_type=tribe_events&ical=1`; RSS `https://www.robootika.ee/yritus/feed/`; JSON `https://www.robootika.ee/wp-json/tribe/events/v1/events` | Estonian educational robotics events: Robolahing, Delta X, FTC Estonia, Folkrace, Võrumaa and Kiili robotics days | **iCal + RSS + JSON (The Events Calendar)** with structured dates | High for robotics | Low: about monthly. On 2026-09-27 it held 6 future events, the newest created 2026-06-19. | The best structured robotics source. In the RSS, pubDate is the event date. |
| **TalTech events** | `https://haldus.taltech.ee/jsonapi/node/event?filter[d][condition][path]=field_event_start_date&filter[d][condition][operator]=>=&filter[d][condition][value]=YYYY-MM-DD&sort=field_event_start_date&page[limit]=50` (HTML: `https://taltech.ee/sundmused`) | All TalTech events: hackathons (Ehitusvaldkonna, MarineHäkk, FREE AGENTS, Fly or Die), the AI competition, stipend contests, Olümpiaadikool | **Drupal JSON:API** with `field_event_start_date`, `title` and `path.alias` (prefix `https://taltech.ee`). A title filter works: `filter[t][condition][path]=title&filter[t][condition][operator]=CONTAINS&filter[t][condition][value]=häkaton` | Medium. Many hackathons are aimed at university students; check each one. | Daily | The public page is a Next.js SPA. The JSON:API is the undocumented backend, so it may change without notice. Useful title keywords: `häkaton`, `Häkk`, `hackathon`, `võistlus`, `gümnaas`. |
| **ETAg event calendar** | iCal `https://www.etag.ee/uritus/?ical=1`; RSS (news) `https://etag.ee/feed/` | Research Council events, including the **teadustööde konkurss deadline** (e.g. `etag.ee/uritus/opilaste-teadustoode-riikliku-konkursi-tahtaeg-4/`) | **iCal** | Low volume but authoritative for the two ETAg contests | Weekly | Mostly research-funding info days, so filter on `õpilas`. |
| **Kesk-Eesti Ettevõtlusinkubaator – Sündmused** | RSS `https://kesk-eesti.ee/sundmus/feed/`; JSON `https://kesk-eesti.ee/wp-json/wp/v2/mec-events` | Regional hackathons: Fellin Häkk, Pärnu CoastHack, Garage48 Future of Wood | **RSS + WP REST (MEC plugin)** | Medium–high. Fellin Häkk and CoastHack are open to 16+ or have no age limit. | A few times a month | The REST `date` is the post date. The event date is in the RSS or the body. |
| **Garage48** | `https://garage48.org/events` | All Garage48 hackathons, in Estonia and abroad | HTML cards with dates. No RSS (`/rss` and `/feed` return 404). | Medium. No minimum age is stated on the pages checked. | Monthly | 2026 events: Future of Wood (16–18.10, Väimela) and kood/Garage48 Empowering Women (16–18.10, Jõhvi, women in Ida-Viru only, English) |
| **JA Eesti calendar** | `https://www.ja.ee/kalender` (Squarespace; `?format=json` returns page JSON, and the dates are in the HTML body) | Õpilasfirma fairs, trainings, the Majandusolümpiaad (04.03 piirkonnavoor, 25.03 lõppvoor), **Eesti parim Õpilasfirma 06.–07.05 (2027)** | HTML list in `dd.mm. – title` format, with **no year** | High for entrepreneurship | Per season | The old WordPress URLs (`ja.ee/sundmused/`, `/feed/`) now return 404. The final is also in the Teaduskool CSV. |
| **Eesti Informaatikaolümpiaad calendar** | `https://eio.ee/Main/Kalender` | 2026–27 season: lahtine 5–11.10, eelvoor 3.12, lõppvoor 6–7.02, BOI 14–18.04 (Norway), plus COCI/USACO pointers | HTML list | High (informatics) | Seasonal (last edited 2026-08-31) | Mostly overlaps with the Teaduskool CSV. Adds COCI and USACO online rounds. |
| **KüberOlümpia** (TalTech, Kaitseministeerium) | `https://sites.google.com/view/kyberolympia/avaleht` | KüberPähkel (grades 4–9), **KüberPuuring (grades 7–12)**, **KüberNaaskel (14–25)** | HTML (Google Sites) | High | Per season (Sept) | The 2026/27 dates are posted. See also `https://ecsc.ee/` for ECSC team selection. |
| **Robotex Eesti** | `https://robotex.ee/` (RSS `https://robotex.ee/feed/` is empty) | Robotex Eesti youth day, 11–12 Dec 2026, T1 Center | HTML | Medium. Categories are mostly for younger pupils. | Yearly | Robotex International RSS (`https://robotex.international/feed/`): the last real post is dated 2025-07-21, so it is stale. |
| **Junction** | `https://2026.hackjunction.com/`, `https://hackjunction.app/hackathons/junction-2026` | Junction main event (Espoo) and JunctionX events | HTML (JS app) | Medium: "Participants are mainly 18 and over." | Yearly | Free for accepted participants. Teams of 1–4. English. |
| **Devpost (online hackathons)** | `https://devpost.com/api/hackathons?status[]=upcoming&status[]=open&challenge_type[]=online` | Worldwide online hackathons (70 open or upcoming on 2026-09-27) | **JSON (unofficial, no auth)** | Low–medium. Eligibility varies by event and is often 18+ or age of majority. | Daily | Filter by theme. Read each event's rules. |
| **CTFtime** | `https://ctftime.org/api/v1/events/?limit=50&start=<unix>&finish=<unix>` | Online CTF competitions worldwide | **JSON** | Medium (cyber practice) | Daily | Useful only if cyber is in scope. |
| **Codeforces** | `https://codeforces.com/api/contest.list?gym=false` | Programming contest rounds | **JSON** | Medium | Daily | Not Estonia-specific. |
| **ERR Novaator** | `https://novaator.err.ee/rss` | Science news, sometimes calls for entries (Rakett69, teadustööd, olympiad results) | **RSS** | Low as an event source | Daily | Keyword-filter on `võistlus`, `konkurss`, `häkaton`. |
| Teaduskool CSV (already used) | `https://tk-kalender-files.s3.eu-central-1.amazonaws.com/kalender.csv` | Olympiads, subject contests, BOI, IOAI | CSV | High | Frequent | Keep as is. Does not cover the items above. |
| kultuuriaken.tartu.ee | e.g. `https://kultuuriaken.tartu.ee/et/syndmus/ahhaa-robolahing-2026` | Tartu events, including Robolahing | HTML with dates. No feed found. | Low | Daily | A good cross-check for Tartu dates. |

Facebook and Instagram (**noted, cannot be scraped**): Garage48 (FB and IG `instagram.com/garage48`), JA Eesti
(`facebook.com/jaeesti`, `instagram.com/jaeesti`), Robotex (`facebook.com/RobotexInternational`), Startup
Estonia FB (it posted Fellin Häkk), Viljandi Linnavalitsus FB and JCI Viljandimaa IG (both posted Fellin
Häkk), and RIA FB. Garage48 and Kesk-Eesti also have newsletters (a subscription form). No Discord server
was found for any Estonian organizer (UNVERIFIED; not searched deeply).

---

## Organizers and their typical events

| Organizer | Events | Typical months (evidence) | Eligibility | Registration / language | Where they announce | Evidence URL | Active? |
|---|---|---|---|---|---|---|---|
| **Garage48** | 48 h hackathons and makeathons (themed); formerly Digiriigi häkaton and Hack the Crisis | Any time. 2026: Future of Wood 16–18 Oct (Väimela); Empowering Women 16–18 Oct (Jõhvi) | No minimum age stated. Empowering Women is for women in Ida-Viru only. | Typeform per event. EN or ET. Empowering Women: registration until 6 Oct. | garage48.org/events, newsletter, FB, IG, Kesk-Eesti feed | https://garage48.org/events ; https://garage48.org/events/empoweringwomen | Yes (Oct 2026) |
| **Kesk-Eesti Ettevõtlusinkubaator** | **Fellin Häkk** (48 h tech hackathon, Viljandi) | Feb: 6–8 Feb 2026 | Open to everyone. No age limit stated. Free, with meals and accommodation. | Google Form. ET. | kesk-eesti.ee/sundmus (RSS), viljandi.ut.ee news, FB | https://kesk-eesti.ee/sundmus/fellin-hakk-2026/ | Yes (2026). Feb 2027 edition UNVERIFIED. |
| **Forwardspace, Pärnumaa Ettevõtlusinkubaator, Pärnu Hub (with EIS)** | **Pärnu CoastHack** (formerly Pärnu HÄKK) | Jan 2026 (Pärnu HÄKK); 2–4 Oct 2026 (CoastHack) | **16+**. Free. Teams form on site. An 18+ mixer is separate. | Typeform `forwardspacehub.typeform.com/to/utrO3W5V`. ET (some EN mentors). | coasthack.ee, Kesk-Eesti feed | https://coasthack.ee/et/ ; https://kesk-eesti.ee/sundmus/parnu-coasthack/ | Yes |
| **Junction (Finland)** | Junction main hackathon | Nov: 13–15 Nov 2026 (Espoo, Hype Areena). Apply by 25 Oct 2026. | "Mainly 18 and over". Free. Teams of 1–4. Limited travel grants. | hackjunction.app. EN. | hackjunction.com, TalTech events (Junction visited TalTech on 16 Sep 2025) | https://2026.hackjunction.com/ ; https://hackjunction.app/hackathons/junction-2026 | Yes |
| **TalTech** (Uusettevõtluse keskus, Mektory, institutes, Mereakadeemia) | Ehitusvaldkonna Häkaton (9–11 Oct 2026); FREE AGENTS AI-agent hackathon (21–22 Apr 2026); **MarineHäkk** (10 Oct 2025; 8 May 2026); Droonihäkaton Fly or Die (18 Oct–1 Nov 2025); Climathon (Oct 2022, 2023); **TalTechi AI-võistlus** (6 Dec 2025, at Robotex) | Oct and Apr–May for hackathons. Dec for the AI competition. | Hackathons are mostly for university students, with gümnaasium entry UNVERIFIED per event. **AI-võistlus: age 16+, secondary and vocational students. The 6 best get conditional admission to the IT faculty.** It was the 2nd edition in 2025. | taltech.ee/sundmused/... ET or EN | TalTech events JSON:API, taltech.ee/uudised | https://taltech.ee/sundmused/ehitusvaldkonna-hakaton ; https://taltech.ee/uudised/taltech-kutsub-ai-voistlusele-mis-avab-ukse-it-teaduskonda ; https://taltech.ee/uusettevotluse-keskus/hakatonid-ja-ideesprindid | Yes. A 2026 AI-võistlus is UNVERIFIED because Robotex International has left. |
| **TalTech Olümpiaadikool** | Kajakas, Physics Cup, majandusvõistlus, koolimatemaatika olümpiaad | Oct–Jan | Gümnaasium | — | taltech.ee/olumpiaadikool; already in the Teaduskool CSV | (Teaduskool CSV rows) | Yes |
| **Enerhack Foundation, Energia Avastuskeskus, TalTech, City of Tallinn** | **EnerHäkaton** (energy ideathon for gümnaasium and vocational students) | Apr: 26–27 Apr 2025 (the first edition) | Gümnaasium and vocational students. Free. No prior knowledge needed. The prize included direct admission to a TalTech energy BSc. | Registration 10 Mar–8 Apr 2025. ET. | estonia.enerhack.org, energiakeskus.ee, tallinn.ee | https://estonia.enerhack.org/lapsed/enerhakaton ; https://www.tallinn.ee/et/ettevotjale/uudis/noored-pakkusid-varskeid-ideid-energiasektori-tuleviku-kujundamiseks | **Uncertain.** No 2026 edition found. |
| **LEEN – Lõuna-Eesti Ettevõtlikud Noored** (county development centres) | **Noorte MõtteHäkk** (1-day social and community hackathon) | Mar: 19 Mar 2025 (Põlva); 25 Mar 2026 (Jõgevamaa). Registration by about 15 Mar. | **Ages 13–21, residents of Jõgeva, Viljandi, Põlva, Tartu, Valga or Võru county.** Teams of 3–5, or solo. Free, with transport. €1000 prize. | Google Form. ET. | ettevotlikudnoored.ee, vorumaa.ee, kysk.ee, arenguagentuur.ee | https://ettevotlikudnoored.ee/blog-posts/mottehakk2026/ | Yes (2026) |
| **Office of the President, TI-Hüpe (AI Leap), EdTech Estonia** | **Presidendi haridushäkaton** (AI in education) | May: 8–9 May 2026 (the first edition) | "Students, teachers, developers, designers…". No age stated. 25 teams are selected. | EventOrnado. Selection rounds 24 and 27 Apr. ET. | hakk.tihupe.ee, tihupe.ee | https://hakk.tihupe.ee/en/ | Yes (2026). A 2027 edition is UNVERIFIED. |
| **JA Eesti / JA Alumni Estonia (with TI-Hüpe)** | Õpilasfirma programme; **Eesti parim Õpilasfirma** (May); Majandusolümpiaad (Mar); **Vibe coding häkaton** (27 Mar, at Nortal Tallinn) | Nov–Dec fairs; Feb national fair; Mar olympiad; May final | Gümnaasium and vocational students. Vibe coding hackathon: €10, solo or team. | Google Form. ET. | ja.ee/kalender, jaalumni.ee, FB, IG | https://www.ja.ee/kalender ; https://www.jaalumni.ee/vibecodinghakaton | Yes. The page for the vibe coding hackathon shows no year; it says "© 2026" and "JA Eesti x TI-Hüppe", so it was most likely 27.03.2026. One summary said 2025, which is UNVERIFIED. |
| **Tehnopol / Prototron** | **Prototron Junior** (idea funding for ages 13–19) | Spring application (a past deadline was "18 March", search only) | 13–19 | — | prototron.ee, tehnopol.ee | https://www.tehnopol.ee/prototron-ootab-saravate-ideedega-kandidaate/ (search only) | **Uncertain.** `https://prototron.ee/en/junior/` returned **404** on 2026-09-27. |
| **Startup Garage, TalTech Uusettevõtluse keskus, Swedbank** | **IGNITER** youth pre-accelerator | Autumn: kick-off 28 Sep 2026 | "noorte" (youth). Age limit UNVERIFIED. | — | TalTech events | https://taltech.ee/sundmused/igniter-kick-youth-pre-acceleration-program-fall-2026 | Yes |
| **Eesti Teadusagentuur (ETAg)** | **Õpilaste teadustööde riiklik konkurss** leading to EUCYS, Regeneron ISEF, Swiss Talent Forum and Taiwan ISC; **Õpilasleiutajate riiklik konkurss** | Teadustööd: deadline **20 Feb** (2026). Festival 22–23 Apr 2026, AHHAA Tartu. Leiutajad: deadline **15 Oct** (2026). | Teadustööd: grades 7–12 (per miks.ee). Leiutajad: ages 6–20. | Submission via etag.ee. ET. | etag.ee (iCal), miks.ee (RSS) | https://miks.ee/opilasele/teadusfestival/opilaste-teadustoode-riiklik-konkurss/ ; https://miks.ee/opilasele/sundmused/opilasleiutajate-riiklik-konkurss-2026/ | Yes |
| **ESERO Eesti / Tartu Observatoorium (UT)** | **CanSat Estonia** | 2025/26 season: phase-1 report 28 Nov 2025; final report 27 Feb 2026; prelim. 19–20 Mar 2026 (Nursi); final 28 Apr 2026 (Nursipalu); ESTEC trip 17–19 Jun 2026 | **Ages 14–19. Teams of 3–6.** | Via ESERO Eesti. ET. Contact tonis.ruutel@ut.ee | kosmos.ut.ee, esero.ee/cansat, miks.ee (CanSat trainings in Aug) | https://kosmos.ut.ee/et/sisu/cansat-estonia-2026 | Yes. The 2026/27 timeline was not yet on that page (UNVERIFIED). |
| **TalTech, Kaitseministeerium, CR14 – KüberOlümpia** | KüberPuuring (grades 7–12); **KüberNaaskel** (14–25; the ECSC selection); KüberPähkel (grades 4–9) | Oct prelims; Nov–Dec finals. 2026: KüberPuuring 1.10–6.11 and final 25.11; KüberNaaskel 5–9.10 and main round 10.12; registration by **30.09.2026** | As listed. ECSC needs Estonian citizenship and age ≤25 on 31 Dec. | Google Forms. ET. | KüberOlümpia site, ecsc.ee, taltech.ee | https://sites.google.com/view/kyberolympia/avaleht ; https://ecsc.ee/ | Yes |
| **CR14** | Küberkotka lend (individual CTF, 16–23) | Jun: pre-qualification 18 Jun and final 29 Jun 2026 (search only) | 16–23 | — | goodnews.ee | https://goodnews.ee/noored-saavad-panna-oma-kotkapilgu-proovile-uuel-kubervoistlusel-kuberkotka-lend/ (search only) | UNVERIFIED on a primary page |
| **Teaduskeskus AHHAA** | **Robolahing** (combat robots, including school heavyweight battles) | Qualification 16 Oct; show **31 Oct 2026** (Tartu Näitused) | School teams take part. Exact rules at robolahing.ee/osalejale/reeglid (not read). | robolahing.ee/registreerimine | robolahing.ee, robootika.ee iCal, miks.ee, kultuuriaken | https://www.robolahing.ee/et ; robootika.ee iCal | Yes (10th edition) |
| **Robotex MTÜ** | Robotex International (**last in Estonia**: 5–6 Dec 2025); **Robotex Eesti** youth day | Dec: 11–12 Dec 2026 (T1) | Many categories. Most are for younger pupils. Folkrace and sumo are open. | Google Forms (robotex.ee); game.robotex.ee (international) | robotex.ee, robotex.international, FB | https://robotex.ee/ ; https://www.tallinn.ee/et/haridus/uudis/robotex-international-2025-viimane-kord-eestis-ja-suurim-tehnoloogiafestival-noortele | Robotex Eesti: yes. International: moved out of Estonia (destination not stated). |
| **UT (Delta), Kiili Gümnaasium, Võrumaa, TTK** | Delta X (16 Jan 2027); FTC Estonia pilot (24 Apr 2027, Kiili); TalTech x TTK Folkrace (3–4 Apr 2026, Mektory); Kiili and Võru robotics days (May) | Jan–May | FTC is typically for grades 7–12 (general FIRST knowledge; not checked). | per event | robootika.ee iCal | https://www.robootika.ee/?post_type=tribe_events&ical=1 ; https://www.robootika.ee/yritus/taltech-x-ttk-folkrace-2026/ | Yes |
| **Eesti Informaatikaolümpiaad / Teaduskool** | EIO rounds, BOI, **IOAI** (tehisaru olümpiaad), Kobras | See the EIO calendar. BOI 14–18 Apr 2027. IOAI Aug. | Gümnaasium | — | eio.ee, Teaduskool CSV | https://eio.ee/Main/Kalender | Yes (in the CSV already) |
| **Hariduskeskus Rakett69 Teadusstuudiod / ERR** | **Rakett69** TV science competition; inspiration camp | Applications close early Sept (7 Sep 2026, search only) | 15–21 as of 1 Jan 2027 (search only) | rakett69.ee | miks.ee, ERR | https://rakett69.ee/ (site "uueks hooajaks uuenemas"); https://miks.ee/uudised/hariduskeskus-rakett69-teadusstuudiod-kutsub-noori-rakett69-inspiratsioonilaagrisse/ | Yes. 2026/27 applications have probably closed (UNVERIFIED). |
| **CERN, DESY, ELSA** | **Beamline for Schools** (international, team proposals) | Proposal deadline in March (13 Mar 2026, search only) | High-school teams. 16+ by the time of the visit. | beamlineforschools.cern. EN. | beamlineforschools.cern | https://beamlineforschools.cern/ (search only) | Yes (UNVERIFIED on the page) |
| **NASA Space Apps** | Global hackathon with local and virtual sites | Oct–Nov: 4–5 Oct 2025; **14–15 Nov 2026** (search only) | Open. Under-18 rules UNVERIFIED. | spaceappschallenge.org. EN. | spaceappschallenge.org | https://www.spaceappschallenge.org/2026/ (search only); Tartu hosted a site in 2019: https://spaceapps2019.ut.ee/ | Global event yes. No Estonian local site found for 2025 or 2026. |
| **Global Game Jam sites (Tallinn: RaRa / GameDev HUB; Tartu: UT CGVR "Pro Game Jam")** | Game jams | Late Jan (UT Pro Game Jam 23–25 Jan 2026) | GGJ Tallinn: open. The Tartu jam is for professionals. | globalgamejam.org | cgvr.cs.ut.ee, inkubaator.tallinn.ee | https://globalgamejam.org/jam-sites/2025/global-gamejam-tallinn ; https://cgvr.cs.ut.ee/university-of-tartu-pro-game-jam-2026/ (search only) | Probably yes (search only) |
| **Peace Child Eesti, Sillamäe Lastekaitse Ühing, Tallinna Haridusamet** | Noorte häkaton "Noorte jätkusuutlikud lahendused" (social projects) | 22–23 May 2025 | Ages 14–26. Teams of 5–10. Free. | Online form. ET. | tallinn.ee | https://www.tallinn.ee/et/huvitegevus-noortele/noorte-hakaton-noorte-jatkusuutlikud-lahendused | Unknown whether it recurs |

---

## Search queries that work (tested 2026-09-27)

Estonian:
- `häkaton gümnaasiumiõpilastele 2026` → EnerHäkaton, MõtteHäkk 2026, VOCO Kaubanduse häkaton, Tamme hackathon
- `noorte häkaton 2025 Eesti gümnasistid` → MõtteHäkk, EnerHäkaton, the Tallinn noorte häkaton, Põlvamaa Rohehäkaton
- `Fellin Häkk Viljandi häkaton` → Fellin Häkk 2026 (kesk-eesti.ee, starhub.ee/fh26)
- `Kesk-Eesti häkaton noored` → MõtteHäkk, the KÜSK Jõgeva–Viljandi youth hackathon (Suure-Jaani)
- `TalTech häkaton gümnasistidele` → the TalTech hackathons page, EnerHäkaton, Tuleviku linna häkaton (2020)
- `küberturbe võistlus gümnaasiumiõpilastele 2026 Eesti CTF noored` → KüberOlümpia, KüberNaaskel, ecsc.ee, Küberkotka lend
- `TalTech Robotiklubi võistlus 2026 gümnaasium robootika` → **robootika.ee events**, the TalTech AI competition
- `Junior Achievement Eesti õpilasfirma võistlus 2026 gümnaasium sündmused` → the JA final, the Vibe coding häkaton
- `CanSat Eesti 2026 võistlus registreerimine` → the CanSat ESTONIA 2026 timeline
- `õpilaste teadustööde riiklik konkurss 2027 tähtaeg ETAg` → the miks.ee and ETAg deadline pages
- `tehisaru häkaton õpilastele 2026 AI Leap TI-Hüpe` → Presidendi haridushäkaton
- `AHHAA Robolahing 2026 kuupäev koolidevahelised lahingud registreerimine` → robolahing.ee, kultuuriaken
- `Rakett69 2026/2027 hooaeg kandideerimine` → rakett69.ee, the miks.ee article
- `tehisintellekti olümpiaad Eesti IOAI valikvõistlus õpilased` → the Teaduskool IOAI pages

English:
- `Garage48 hackathon 2026` → the Garage48 events
- `Junction 2026 hackathon Helsinki dates` → hackjunction.app, 2026.hackjunction.com
- `Robotex International 2025 detsember Tallinn` (mixed ET and EN) → the news that it was the last edition in Estonia
- `Startup Estonia events calendar hackathons Estonia 2026` → MarineHäkk (TalTech), the Startup Estonia events page
- `NASA Space Apps Challenge 2026 dates` → the 14–15 Nov 2026 dates
- `CERN Beamline for Schools 2027 competition deadline` → BL4S rules

Queries that found **nothing useful**: `"HackTheSchool" OR "Hack the School" Estonia` (only EdTech Hack and an
Italian event), `Riigi Infosüsteemi Amet häkaton OR hackathon 2025` (only 2018–2019 Digiriigi häkatons),
`Robotex International 2026 dates hackathon` (Georgia Tech results), `hackathon Tallinn 2026 lu.ma`,
`Hack Club Tallinn …`, `Stockholm Junior Water Prize Eesti 2026` (no Estonian round found),
`Euroopa statistikavõistlus 2026` (Estonian pages only up to 2020).

Tip: adding a county or town (`Pärnu`, `Viljandi`, `Jõgeva`, `Tartu`) plus `häkaton` finds regional
events that the national queries miss.

---

## Dead or unsuitable

| Source or organizer | Reason | Checked on |
|---|---|---|
| Eventbrite `eventbrite.com/d/estonia/hackathon/` | Only recruiting "HackerX" and "WomenHack" employer tickets. No real hackathons. | https://www.eventbrite.com/d/estonia/hackathon/ |
| hackathon.com (Tallinn) | "There is no upcoming hackathons found in 'Tallinn, Estonia'" | https://www.hackathon.com/city/estonia/tallinn |
| Startup Estonia `our-events` | Only Startup Estonia's own events (Latitude59, sTARTUp Day, awards). No hackathons. No feed. | https://startupestonia.ee/our-events/ |
| Hack Club events API | `https://events.hackclub.com/api/events/upcoming/` returned an empty list. No Estonian Scrapyard, Daydream or Counterspell site was found. | curl |
| Luma | No `luma.com/estonia` or `/tallinn` page (404). Only single events (e.g. Cursor Hackathon Tallinn, 14 Aug 2025). | curl; search |
| RIA (Riigi Infosüsteemi Amet) hackathons | The Digiriigi häkaton series ran 2018–2019 (and a TalTech "Digiriigi Tulevikulahenduste Häkaton" on 10–11 Oct 2023). Nothing newer, and it was not aimed at students. | https://garage48.org/events/digiriigi-hakaton-3-0 ; TalTech JSON:API |
| Hack the Crisis / Accelerate Estonia | A one-off: Hack the Crisis ran on 13–15 Mar 2020 (Accelerate Estonia with Garage48). Accelerate Estonia is a government innovation programme and does not run youth competitions (no current events found). | https://garage48.org/events/hack-the-crisis (search only) |
| "HackTheSchool" | No Estonian event by that name exists. The closest are EdTech Hack (TLÜ, 2022–2024) and the Presidendi haridushäkaton. | search |
| Harno (Haridus- ja Noorteamet) `harno.ee/sundmused` | Events for teachers and youth workers. Its hackathons (e.g. õpetajate järelkasvu häkaton) are for teachers. The RSS `/rss-feeds/rss.xml` is news. | https://harno.ee/sundmused |
| HTM `hm.ee/sundmused` | Ministry events. No student competitions and no dates in the HTML. The RSS is news. | https://www.hm.ee/sundmused |
| UT `ut.ee/et/uritused` | A JS-rendered list of defences and lectures. No feed found. Teaduskool already covers UT's student events. | curl |
| Robotex International RSS | Stale: the last real post is 2025-07-21, and the event has left Estonia. Use robotex.ee instead. | https://robotex.international/feed/ |
| TI-Hüpe `tihupe.ee/feed/` | Returns HTTP 500. The student programme page has no dated events. | https://tihupe.ee/opilaste-programm/ |
| ja.ee old URLs (`/sundmused/`, `/feed/`) | 404 since the move to Squarespace. Use `https://www.ja.ee/kalender`. | curl |
| prototron.ee/en/junior/ | 404. Prototron Junior's status is unclear. | WebFetch |
| VOCO Kaubanduse häkaton (19 Mar 2026) | For vocational students (VOCO plus guest vocational schools). | https://voco.ee/kaubanduse-hakaton-2026-toi-kokku-noored-ja-ettevotted-uhiseid-lahendusi-looma/ (search only) |
| Tartu Tamme Gümnaasium häkaton | An internal event for that school's 10th and 11th graders. | https://tammegymnaasium.ee/hakaton/ (search only) |
| kood/Garage48 Empowering Women | Women in Ida-Viru only. Fits only if the user matches. | https://garage48.org/events/empoweringwomen |
| Euroopa statistikavõistlus (Statistikaamet) | Estonian pages only for 2019–2020. No evidence of later rounds (UNVERIFIED). | search results on stat.ee |
| Stockholm Junior Water Prize – Estonia | No Estonian national round found. | search |
| WRO (World Robot Olympiad) Estonia | The international Future Engineers category is for ages 14–19 (per wro-association.org), but no Estonian national round was found. UNVERIFIED. | https://wro-association.org/competition/2026-season/ (search only) |
| MLH (Major League Hacking) | The season page is JS-rendered, with no Estonian or Baltic events visible. UNVERIFIED. | curl mlh.io/seasons/2027/events |

---

## Verification log (all checked 2026-09-27)

- Teaduskool CSV contents: curl of the S3 CSV and grep (see Summary).
- Garage48 events list and lack of a feed: WebFetch of https://garage48.org/events; curl `/rss` and `/feed` returned 404.
- EnerHäkaton 2025: https://estonia.enerhack.org/lapsed/enerhakaton ; tallinn.ee news (the "first EnerHäkaton").
- MõtteHäkk 2026: https://ettevotlikudnoored.ee/blog-posts/mottehakk2026/
- Fellin Häkk 2026: https://kesk-eesti.ee/sundmus/fellin-hakk-2026/
- Kesk-Eesti feeds: curl `https://kesk-eesti.ee/sundmus/feed/` (RSS, items: Pärnu CoastHack, Garage48 Future of Wood) and `/wp-json/wp/v2/mec-events`.
- Pärnu CoastHack: https://coasthack.ee/et/ ; https://kesk-eesti.ee/sundmus/parnu-coasthack/
- Junction 2026: https://hackjunction.app/hackathons/junction-2026 ; https://2026.hackjunction.com/
- Robotex: https://robotex.international/roboticscompetitions/ ; RSS via curl; https://www.tallinn.ee/et/haridus/uudis/robotex-international-2025-viimane-kord-eestis-ja-suurim-tehnoloogiafestival-noortele ; https://robotex.ee/
- TalTech hackathons page: https://taltech.ee/uusettevotluse-keskus/hakatonid-ja-ideesprindid ; TalTech JSON:API queries (title contains `häkaton`, `Häkk`, `võistlus`; start date ≥ 2026-09-27).
- TalTech AI-võistlus: https://taltech.ee/uudised/taltech-kutsub-ai-voistlusele-mis-avab-ukse-it-teaduskonda
- Presidendi haridushäkaton: https://hakk.tihupe.ee/en/
- Tallinn noorte häkaton 2025: https://www.tallinn.ee/et/huvitegevus-noortele/noorte-hakaton-noorte-jatkusuutlikud-lahendused
- Miks.ee feeds and content: curl of `/opilasele/sundmused/feed/`, `/wp-json/wp/v2/events`, `/wp-json/wp/v2/event_cat`; https://miks.ee/opilasele/konkursid/ ; https://miks.ee/opilasele/teadusfestival/opilaste-teadustoode-riiklik-konkurss/
- ETAg iCal: curl of `https://www.etag.ee/uritus/?ical=1` (text/calendar with VEVENTs).
- CanSat: https://kosmos.ut.ee/et/sisu/cansat-estonia-2026
- JA Eesti: curl of https://www.ja.ee/kalender (the dated list); https://www.jaalumni.ee/vibecodinghakaton
- KüberOlümpia and ECSC: https://sites.google.com/view/kyberolympia/avaleht ; https://ecsc.ee/
- robootika.ee: curl of the iCal, RSS and REST endpoints.
- Robolahing: https://www.robolahing.ee/et (31 Oct 2026); https://kultuuriaken.tartu.ee/et/syndmus/ahhaa-robolahing-2026 (31 Oct 2026, 12:00–21:30). **Correction:** a search snippet said "8. novembril". That is probably the 2025 date, and the 2026 pages say 31 October.
- EIO calendar: curl of https://eio.ee/Main/Kalender
- Devpost, CTFtime and Codeforces APIs: curl, each returned JSON.
- Startup Estonia, Eventbrite, hackathon.com: WebFetch (see Dead or unsuitable).
