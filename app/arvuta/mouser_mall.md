# Mouser tellimus

- **Projekt:**
- **Kuupäev:** {{kuupäev}}

## Komponendid

Kleebi Mouseri link (või Mouseri osa number, nt 595-NE555P) veergu **Link** ja kirjuta **Kogus**: ülejäänu täitub ise. Teiste poodide lingid jäävad lingiks, nende info kirjuta ise. Hinnad ja laoseisu värskendab ▶.

| Link | Kogus | Mouser nr | Tootja nr | Tootja | Kirjeldus | Ühikuhind (€) | Summa (€) | Laos | Hoiatus |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  | 1 |  |  |  |  |  |  |  |  |

**Kokku ilma käibemaksuta:** `= euro(kokku)`

```arvuta-peida
t = tabel("Komponendid")
m = mouser(t["Link"], t["Kogus"])
for veerg, allikas in [("Mouser nr", "nr"), ("Tootja nr", "tootja_nr"), ("Tootja", "tootja"),
                       ("Kirjeldus", "kirjeldus"), ("Ühikuhind (€)", "hind"), ("Laos", "laos")]:
    t[veerg] = m[allikas].where(m[allikas].notna(), t[veerg])
t["Hoiatus"] = m["hoiatus"].fillna("")
t["Summa (€)"] = t["Kogus"] * t["Ühikuhind (€)"]
kokku = round(t["Summa (€)"].sum(), 2)
# Viimases reas on link: uus tühi rida järgmise osa jaoks.
viimane = t["Link"].iloc[-1] if len(t) else ""
if isinstance(viimane, str) and viimane.strip():
    t.loc[len(t)] = {"Link": "", "Kogus": 1}
```
