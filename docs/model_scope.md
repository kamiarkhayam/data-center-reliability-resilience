# Scope of the revised study

## Network and demand

The synthetic testbed contains 7,114 nodes, 7,215 edges, 4,179 community-load records, three regional modules and 27 feeders. A feeder-level largest-load ranking assigns 204 critical records to 201 nodes, representing approximately 248 MW of 537.6 MW baseline community demand. This is a synthetic critical-service overlay, not a utility inventory.

Adequacy and chronological assessments use the native 35,040 quarter-hour profile (approximately 461.36 MW peak and 313.45 MW mean). Fixed-repair hurricane assessments use baseline load. DC demand is separate.

## Supply adequacy

An IEEE RTS 32-unit fleet is scaled by native community peak and the fixed 1.25 planning factor. Zero to five independently available U100-equivalent units add 0–101.17 MW. No-DC and DC cases receive identical supply at each level. Critical community demand is served first; remaining capacity is shared proportionally between noncritical community demand and DC utility requests.

Private backup serves internal shortfalls without reducing the utility demand request. Coordinated self-supply and export are disabled. EENS and shortfall hours are exact stationary expectations under independent unit outages. Dividing shortfall hours by 24 does not give daily LOLE.

## Emergency support

Three nonoverlapping eligible load sets receive support through separate 5 MW site limits. Each site has 13 × 2.725 MW generators, 1,440 MWh electrical-equivalent fuel and an IT-only 20 MW / 3.333 MWh UPS. Operating/design demands are 26/30 MW. Export reserves full internal-demand generation headroom plus one contingency unit and follows critical-first allocation after a 15-minute authorization delay.

The retained floor is constant in time. A 44-hour design-load reserve retains 1,320 MWh/site, leaving 120 MWh initially exportable. Internal backup can use retained fuel. No fuel deliveries occur during hurricane restoration. Reserve hours differ from the old E12/E24 dedicated export budgets.

## Recovery and internal service

The primary 131-scenario comparison holds archived GraphRL-A2C repair orders and durations fixed. DC utility supply remains available. Export changes service before reconnection, not repair timing. Continuous emergency-call equipment histories are paired between configurations; inherited bundled-link connectivity is retained.

The separate 960-pair chronological check includes supply outages, dispatch-dependent generator exposure, UPS recharge and persistent resource states through the year. A 168-hour warmup precedes 8,760 reporting hours; the hurricane starts halfway through reporting. Export pauses without full internal utility service. Post-restoration outcomes are included in full-year outcomes. Interruption frequencies are conditional on the evaluated sample and operating assumptions.

The SI support-responsive comparison uses the same frozen policy at both demands, equipment history zero and zero reserve. It observes current support, not future equipment transitions.

## Economics and boundaries

Economics values export enablement from the same connected portfolio. Load-level damage is calculated before averaging. Illustrative reference inputs are $1 million capital, $25,000/year fixed costs, $300/MWh export costs, 20 years, 3.1% real discount and a 10-year representative event interval. The interval is not an estimated hurricane return period.

Routine DC construction/operation, taxes, jobs and host-community fiscal balances are outside this comparison. Emissions damages, replacements and salvage are unpriced. Old calculations remain in [legacy scope](legacy_model_scope.md).

Support paths and source connections are assumed available except in explicit connection-removal tests. The framework does not resolve AC power flow, protection design, switching, crew travel, cooling-related IT shutdown or weather-correlated upstream outages. Hurricane-track labels identify synthetic damage provenance, not observed utility interruptions.
