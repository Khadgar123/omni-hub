---
page_type: synthesis
domain: finance
claim_ids: ["fe0c82e8de234332", "cbb6cff6b91e8309", "1d294e2b021a5d3a", "11a770bc125e9a55", "9d56d909ab5a7202", "4b8a88496ecb4a64", "b295d23faf38b545", "0fc62591251eee55", "96bddbcc49d29633", "5c5437745aa122e6", "173e7833ea199998", "a135c8a67b9e5715", "c8a76999999595bf", "12f50ccdbdb56a45", "43819e8c65508280", "330b546b0635f522", "7c9b5e5c217cda36"]
source_ids: ["fred:CPIAUCSL", "fred:RRSFS", "fred:AMBSLREAL", "fred:MZMREAL", "fred:UNRATE", "fred:DFF", "fred:DGS10", "fred:T10YIE", "fred:T10YIEM", "fred:GDPC1", "fred:M2SL", "fred:M2", "edgar:0001752724-25-120712:primary_doc.xml", "edgar:0001193125-19-295695:d835657dex51.htm", "edgar:0001752724-22-243083:vg_massachusettstaxexempt.htm", "edgar:0001752724-21-228854:primary_doc.xml", "edgar:0001104659-24-128007:tm2427971d2_ex5-2.htm", "edgar:0001193125-07-205198:dfwp.htm", "edgar:0001752724-22-098272:vg_massachusettstaxexempt.htm", "edgar:0001193125-16-775205:d273806dex51.htm"]
t_valid_from: 2026-05-29T04:52:55.112939+00:00
t_valid_to: null
superseded_by: null
confidence: medium
review_state: proposed
ingest_run_id: seed-orchestrator-20260529-014911
---

# seed:seed-orchestrator-20260529-014911

## Question

seed:seed-orchestrator-20260529-014911

## Sources

- [R1] **Consumer Price Index for All Urban Consumers: All Items in U.S. City Average** — fred — https://fred.stlouisfed.org/series/CPIAUCSL
- [R2] **Advance Real Retail and Food Services Sales** — fred — https://fred.stlouisfed.org/series/RRSFS
- [R3] **Real St. Louis Adjusted Monetary Base (DISCONTINUED)** — fred — https://fred.stlouisfed.org/series/AMBSLREAL
- [R4] **Real MZM Money Stock (DISCONTINUED)** — fred — https://fred.stlouisfed.org/series/MZMREAL
- [R5] **Unemployment Rate** — fred — https://fred.stlouisfed.org/series/UNRATE
- [R6] **Federal Funds Effective Rate** — fred — https://fred.stlouisfed.org/series/DFF
- [R7] **Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, Quoted on an Investment Basis** — fred — https://fred.stlouisfed.org/series/DGS10
- [R8] **10-Year Breakeven Inflation Rate** — fred — https://fred.stlouisfed.org/series/T10YIE
- [R9] **10-Year Breakeven Inflation Rate** — fred — https://fred.stlouisfed.org/series/T10YIEM
- [R10] **Real Gross Domestic Product** — fred — https://fred.stlouisfed.org/series/GDPC1
- [R11] **M2** — fred — https://fred.stlouisfed.org/series/M2SL
- [R12] **M2 (DISCONTINUED)** — fred — https://fred.stlouisfed.org/series/M2
- [R13] **NPORT-P — MFS MUNICIPAL SERIES TRUST  (CIK 0000751656)** — edgar — https://www.sec.gov/Archives/edgar/data/0000751656/000175272425120712:primary_doc.xml/0001752724-25-120712:primary_doc.xml-index.htm
- [R14] **S-3ASR — AXIS CAPITAL HOLDINGS LTD  (AXS, AXS-PE)  (CIK 0001214816)** — edgar — https://www.sec.gov/Archives/edgar/data/0001214816/000119312519295695:d835657dex51.htm/0001193125-19-295695:d835657dex51.htm-index.htm
- [R15] **NPORT-P — VANGUARD MASSACHUSETTS TAX-EXEMPT FUNDS  (CIK 0001070414)** — edgar — https://www.sec.gov/Archives/edgar/data/0001070414/000175272422243083:vg_massachusettstaxexempt.htm/0001752724-22-243083:vg_massachusettstaxexempt.htm-index.htm
- [R16] **NPORT-P — NUVEEN MASSACHUSETTS QUALITY MUNICIPAL INCOME FUND  (NMT)  (CIK 0000897419)** — edgar — https://www.sec.gov/Archives/edgar/data/0000897419/000175272421228854:primary_doc.xml/0001752724-21-228854:primary_doc.xml-index.htm
- [R17] **S-3ASR — LyondellBasell Industries N.V.  (LYB)  (CIK 0001489393)** — edgar — https://www.sec.gov/Archives/edgar/data/0001489393/000110465924128007:tm2427971d2_ex52.htm/0001104659-24-128007:tm2427971d2_ex5-2.htm-index.htm
- [R18] **FWP — HSBC Home Equity Loan CORP II  (CIK 0001363894)** — edgar — https://www.sec.gov/Archives/edgar/data/0001363894/000119312507205198:dfwp.htm/0001193125-07-205198:dfwp.htm-index.htm
- [R19] **NPORT-P — VANGUARD MASSACHUSETTS TAX-EXEMPT FUNDS  (CIK 0001070414)** — edgar — https://www.sec.gov/Archives/edgar/data/0001070414/000175272422098272:vg_massachusettstaxexempt.htm/0001752724-22-098272:vg_massachusettstaxexempt.htm-index.htm
- [R20] **S-3ASR — AXIS CAPITAL HOLDINGS LTD  (AXS, AXS-PE)  (CIK 0001214816)** — edgar — https://www.sec.gov/Archives/edgar/data/0001214816/000119312516775205:d273806dex51.htm/0001193125-16-775205:d273806dex51.htm-index.htm

## Compiled Findings

- [R1] The Consumer Price Index for All Urban Consumers: All Items (CPIAUCSL) is a price index of a basket of goods and services paid by urban consumers. Percent changes in the price index measure the inflation rate between any two time periods. The most common inflation metric is the percent change from one year ago. It can also represent the buying habits of urban consumers. This particular index includes roughly 88 percent of the total population, accounting for wage earners, clerical workers, techn
- [R2] The data in this series are calculated using two series, and as such only update when those series update. This series is constructed from Advance Retail and Food Services Sales (RSAFS (https://fred.stlouisfed.org/series/RSAFS)) deflated using the Consumer Price Index for All Urban Consumers (1982-84=100) (CPIAUCSL (https://fred.stlouisfed.org/series/CPIAUCSL)).
- [R3] This series deflates St. Louis Adjusted Monetary Base (AMBSL) (https://fred.stlouisfed.org/series/AMBSL) with Consumer Price Index (CPIAUCSL) (https://fred.stlouisfed.org/series/CPIAUCSL). 

Updates of this series will be ceased on December 20, 2019. Interested users can access customized version of this series (https://fred.stlouisfed.org/graph/?g=pBKi) that's using Total Monetary Base (BOGMBASE) (https://fred.stlouisfed.org/series/BOGMBASE) instead of the St. Louis Adjusted Monetary Base (AM
- [R4] This series has been discontinued and will no longer be updated. The institutional money market funds component (IMFSL (https://fred.stlouisfed.org/series/IMFSL)) used to calculate MZM has been discontinued by the Board of Governors and is no longer available in the H.6 statistical release, Money Stock Measures.

For further information about the changes to the H.6 statistical release, please see the announcements (https://www.federalreserve.gov/feeds/h6.html) provided by the source.

This serie
- [R5] The unemployment rate represents the number of unemployed as a percentage of the labor force. Labor force data are restricted to people 16 years of age and older, who currently reside in 1 of the 50 states or the District of Columbia, who do not reside in institutions (e.g., penal and mental facilities, homes for the aged), and who are not on active duty in the Armed Forces.

This rate is also defined as the U-3 measure of labor underutilization.

The series comes from the 'Current Populatio
- [R6] Daily Federal Funds Rate from 1928-1954 (https://fred.stlouisfed.org/categories/33951).

The federal funds rate is the interest rate at which depository institutions trade federal funds (balances held at Federal Reserve Banks) with each other overnight. When a depository institution has surplus balances in its reserve account, it lends to other banks in need of larger balances. In simpler terms, a bank with excess cash, which is often referred to as liquidity, will lend to another bank that nee
- [R7] H.15 Statistical Release (https://www.federalreserve.gov/releases/h15/current/h15.pdf) notes and Treasury Yield Curve Methodology (https://www.treasury.gov/resource-center/data-chart-center/interest-rates/Pages/yieldmethod.aspx).

For questions on the data, please contact the data source (https://www.federalreserve.gov/apps/ContactUs/feedback.aspx?refurl=/releases/h15/%). For questions on FRED functionality, please contact us here (https://fred.stlouisfed.org/contactus/).</p>
- [R8] The breakeven inflation rate represents a measure of expected inflation derived from 10-Year Treasury Constant Maturity Securities (DGS10 (https://fred.stlouisfed.org/series/DGS10)) and 10-Year Treasury Inflation-Indexed Constant Maturity Securities (DFII10 (https://fred.stlouisfed.org/series/DFII10)). The latest value implies what market participants expect inflation to be in the next 10 years, on average.
Starting with the update on June 21, 2019, the Treasury bond data used in calculating in
- [R9] The breakeven inflation rate represents a measure of expected inflation derived from 10-Year Treasury Constant Maturity Securities (DGS10 (https://fred.stlouisfed.org/series/DGS10)) and 10-Year Treasury Inflation-Indexed Constant Maturity Securities (DFII10 (https://fred.stlouisfed.org/series/DFII10)). The latest value implies what market participants expect inflation to be in the next 10 years, on average.
Starting with the update on June 21, 2019, the Treasury bond data used in calculating in
- [R10] BEA Account Code: A191RX

Real gross domestic product is the inflation adjusted value of the goods and services produced by labor and property located in the United States.For more information see the Guide to the National Income and Product Accounts of the United States (NIPA). For more information, please visit the Bureau of Economic Analysis (http://www.bea.gov/national/pdf/nipaguid.pdf).
- [R11] announcements (https://www.federalreserve.gov/feeds/h6.html) and Technical Q&As (https://www.federalreserve.gov/releases/h6/h6_technical_qa.htm) posted on December 17, 2020.

For questions on the data, please contact the data source (https://www.federalreserve.gov/apps/ContactUs/feedback.aspx?refurl=/releases/h6/%). For questions on FRED functionality, please contact us here (https://fred.stlouisfed.org/contactus/).</p>
- [R12] WM2NS (https://fred.stlouisfed.org/series/WM2NS), and the seasonally adjusted monthly series is M2SL (https://fred.stlouisfed.org/series/M2SL).

Starting on February 23, 2021, the H.6 statistical release is now published at a monthly frequency and contains only monthly average data needed to construct the monetary aggregates. Weekly average, non-seasonally adjusted data will continue to be made available, while weekly average, seasonally adjusted data will no longer be provided. For further info
- [R13] MFS MUNICIPAL SERIES TRUST  (CIK 0000751656)
- [R14] AXIS CAPITAL HOLDINGS LTD  (AXS, AXS-PE)  (CIK 0001214816), AXIS Specialty Finance PLC  (CIK 0001595089), AXIS Specialty Finance LLC  (CIK 0001487427)
- [R15] VANGUARD MASSACHUSETTS TAX-EXEMPT FUNDS  (CIK 0001070414)
- [R16] NUVEEN MASSACHUSETTS QUALITY MUNICIPAL INCOME FUND  (NMT)  (CIK 0000897419)
- [R17] LyondellBasell Industries N.V.  (LYB)  (CIK 0001489393), LYB International Finance II B.V.  (CIK 0001667286), LYB International Finance III LLC  (CIK 0001732788)
- [R18] HSBC Home Equity Loan CORP II  (CIK 0001363894)
- [R19] VANGUARD MASSACHUSETTS TAX-EXEMPT FUNDS  (CIK 0001070414)
- [R20] AXIS CAPITAL HOLDINGS LTD  (AXS, AXS-PE)  (CIK 0001214816), AXIS Specialty Finance PLC  (CIK 0001595089), AXIS Specialty Finance LLC  (CIK 0001487427)

## Candidate Claims

- `fe0c82e8de234332` (0.50) The Consumer Price Index for All Urban Consumers: All Items (CPIAUCSL) is a price index of a basket of goods and services paid by urban consumers.
- `cbb6cff6b91e8309` (0.50) The data in this series are calculated using two series, and as such only update when those series update.
- `1d294e2b021a5d3a` (0.50) This series deflates St.
- `11a770bc125e9a55` (0.50) This series has been discontinued and will no longer be updated.
- `9d56d909ab5a7202` (0.50) The unemployment rate represents the number of unemployed as a percentage of the labor force.
- `4b8a88496ecb4a64` (0.50) Daily Federal Funds Rate from 1928-1954 (https://fred.stlouisfed.org/categories/33951).
- `b295d23faf38b545` (0.50) H.15 Statistical Release (https://www.federalreserve.gov/releases/h15/current/h15.pdf) notes and Treasury Yield Curve Methodology (https://www.treasury.gov/resource-center/data-chart-center/interest-rates/Pages/yieldmethod.aspx).
- `0fc62591251eee55` (0.50) The breakeven inflation rate represents a measure of expected inflation derived from 10-Year Treasury Constant Maturity Securities (DGS10 (https://fred.stlouisfed.org/series/DGS10)) and 10-Year Treasury Inflation-Indexed Constant Maturity Securities (DFII10 (https://fred.stlouisfed.org/series/DFII10)).
- `96bddbcc49d29633` (0.50) BEA Account Code: A191RX Real gross domestic product is the inflation adjusted value of the goods and services produced by labor and property located in the United States.For more information see the Guide to the National Income and Product Accounts of the United States (NIPA).
- `5c5437745aa122e6` (0.50) announcements (https://www.federalreserve.gov/feeds/h6.html) and Technical Q&As (https://www.federalreserve.gov/releases/h6/h6_technical_qa.htm) posted on December 17, 2020.
- `173e7833ea199998` (0.50) WM2NS (https://fred.stlouisfed.org/series/WM2NS), and the seasonally adjusted monthly series is M2SL (https://fred.stlouisfed.org/series/M2SL).
- `a135c8a67b9e5715` (0.50) MFS MUNICIPAL SERIES TRUST (CIK 0000751656)
- `c8a76999999595bf` (0.50) AXIS CAPITAL HOLDINGS LTD (AXS, AXS-PE) (CIK 0001214816), AXIS Specialty Finance PLC (CIK 0001595089), AXIS Specialty Finance LLC (CIK 0001487427)
- `12f50ccdbdb56a45` (0.50) VANGUARD MASSACHUSETTS TAX-EXEMPT FUNDS (CIK 0001070414)
- `43819e8c65508280` (0.50) NUVEEN MASSACHUSETTS QUALITY MUNICIPAL INCOME FUND (NMT) (CIK 0000897419)
- `330b546b0635f522` (0.50) LyondellBasell Industries N.V.
- `7c9b5e5c217cda36` (0.50) HSBC Home Equity Loan CORP II (CIK 0001363894)

## Evidence Files

- `vault/evidence/finance/seed-orchestrator-20260529-014911__001__deb3d66f8f.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__002__5d09c92546.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__003__43ed09ecfd.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__004__0385a1b4bf.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__005__9d03984317.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__006__10404f46ec.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__007__53b12b38e2.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__008__047417073b.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__009__5125ebc87d.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__010__72f6aaa114.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__011__27b1d23510.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__012__2a190be7eb.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__013__dae37a93ed.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__014__1664c45a1a.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__015__459670973e.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__016__0796bb0a48.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__017__36431f52d4.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__018__e6d925ff75.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__019__f5a26b3d83.json`
- `vault/evidence/finance/seed-orchestrator-20260529-014911__020__69ed1dab74.json`

## References

- [R1] fred · fred:CPIAUCSL · https://fred.stlouisfed.org/series/CPIAUCSL
- [R2] fred · fred:RRSFS · https://fred.stlouisfed.org/series/RRSFS
- [R3] fred · fred:AMBSLREAL · https://fred.stlouisfed.org/series/AMBSLREAL
- [R4] fred · fred:MZMREAL · https://fred.stlouisfed.org/series/MZMREAL
- [R5] fred · fred:UNRATE · https://fred.stlouisfed.org/series/UNRATE
- [R6] fred · fred:DFF · https://fred.stlouisfed.org/series/DFF
- [R7] fred · fred:DGS10 · https://fred.stlouisfed.org/series/DGS10
- [R8] fred · fred:T10YIE · https://fred.stlouisfed.org/series/T10YIE
- [R9] fred · fred:T10YIEM · https://fred.stlouisfed.org/series/T10YIEM
- [R10] fred · fred:GDPC1 · https://fred.stlouisfed.org/series/GDPC1
- [R11] fred · fred:M2SL · https://fred.stlouisfed.org/series/M2SL
- [R12] fred · fred:M2 · https://fred.stlouisfed.org/series/M2
- [R13] edgar · edgar:0001752724-25-120712:primary_doc.xml · https://www.sec.gov/Archives/edgar/data/0000751656/000175272425120712:primary_doc.xml/0001752724-25-120712:primary_doc.xml-index.htm
- [R14] edgar · edgar:0001193125-19-295695:d835657dex51.htm · https://www.sec.gov/Archives/edgar/data/0001214816/000119312519295695:d835657dex51.htm/0001193125-19-295695:d835657dex51.htm-index.htm
- [R15] edgar · edgar:0001752724-22-243083:vg_massachusettstaxexempt.htm · https://www.sec.gov/Archives/edgar/data/0001070414/000175272422243083:vg_massachusettstaxexempt.htm/0001752724-22-243083:vg_massachusettstaxexempt.htm-index.htm
- [R16] edgar · edgar:0001752724-21-228854:primary_doc.xml · https://www.sec.gov/Archives/edgar/data/0000897419/000175272421228854:primary_doc.xml/0001752724-21-228854:primary_doc.xml-index.htm
- [R17] edgar · edgar:0001104659-24-128007:tm2427971d2_ex5-2.htm · https://www.sec.gov/Archives/edgar/data/0001489393/000110465924128007:tm2427971d2_ex52.htm/0001104659-24-128007:tm2427971d2_ex5-2.htm-index.htm
- [R18] edgar · edgar:0001193125-07-205198:dfwp.htm · https://www.sec.gov/Archives/edgar/data/0001363894/000119312507205198:dfwp.htm/0001193125-07-205198:dfwp.htm-index.htm
- [R19] edgar · edgar:0001752724-22-098272:vg_massachusettstaxexempt.htm · https://www.sec.gov/Archives/edgar/data/0001070414/000175272422098272:vg_massachusettstaxexempt.htm/0001752724-22-098272:vg_massachusettstaxexempt.htm-index.htm
- [R20] edgar · edgar:0001193125-16-775205:d273806dex51.htm · https://www.sec.gov/Archives/edgar/data/0001214816/000119312516775205:d273806dex51.htm/0001193125-16-775205:d273806dex51.htm-index.htm

## Ingest Metadata

- run_id: `seed-orchestrator-20260529-014911`
- fusion: seed
- sources_succeeded: edgar, fred
- record_count: 20
