# Parameter sources and study choices

[The parameter ledger](provenance/revision_parameter_sources.json) preserves the source URLs, locators, adaptations and explicit assumptions from the audited physical-model records. These records distinguish source-supported inputs from the synthetic three-site construction.

| Input | Provenance and interpretation |
|---|---|
| Distribution assets and native profiles | SMART-DS/OpenDSS P49U assets; three-module composition and critical-load ranking are study constructions |
| Upstream fleet and outage times | IEEE Reliability Test System; capacity scaling and additions are fixed study planning choices |
| IT load and operating/design PUE | STACK facility application; equal three-site portfolio is synthetic |
| Generator rating, fuel and UPS archetype | California Energy Commission Laurelwood filings; sizing is derived for the study design demand |
| Initial generator availability, start failure, running lifetime and repair | NREL backup-generation reliability report; independent exponential running/repair times are model assumptions |
| Readiness and load-acceptance timing | Laurelwood responses; consecutive 90-second allowances are a study approximation |
| UPS recharge | Eaton VRLA guidance translated to 1.8 MW effective charging to 90% capacity |
| Export authorization, 5 MW connections and retained reserves | Study operating assumptions; reserve hours use 30 MW design demand, not export rating |
| Fuel deliveries | Laurelwood logistics inform lead time; threshold, successful delivery and hurricane suspension are study assumptions |
| Interruption valuation | `configs/economics/interruption_damage.yaml` retains sector/duration sources and the critical-service planning value |
| Incremental capital, fixed and variable export costs | Illustrative study inputs, not procurement estimates |
| Discount rate | 3.1% real reference adopted from FEMA's 2024 guidance; held fixed for this assessment |

The original source records also contain coordinated-response context used in earlier exploratory analyses. The current main adequacy comparison disables coordinated self-supply, and the selected chronological check uses private backup. No cited source specifies the complete synthetic operating policy.
