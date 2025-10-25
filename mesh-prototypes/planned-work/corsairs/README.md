# Corsair or Isle-Mesh Apps

An application that is meant to operate specifically via reticulum and navigate/cross between different vpn-based archipelagos.

Corsair Apps operate on the assumption that every instance of itself is a 'version-controlled-state' of some kind,
in this way each archipelago lessens the overall burden of sending information over the mesh-network by sending
only the necessary state-change deltas.  Hashed deltas and delta-aggregates at the archipelago level can act as the means
for efficiently relaying information back to the app-source, known as the 'corsair app' which has a particular X.509 based
identity using Reticulum.

## State Management and Relays

The core corsair app should be capable of keeping a repository of valid-states, and valid-state-relays.

Current-State : The latest state with all deltas and sub-delta-trees that have been accounted for in the latest state-update.

Valid-States : A set of records or recoverable-via-deltas-records of valid states that have been sent out from the core app.

## State Deltas and Delta Undercurrents

Undercurrents : Fully e2e encrypted streams of app-state-deltas over reticulum, which may aggregate in a heirarchal fashion
as they grow closer to the source.  However, we may choose to disguise at each level how many downstream app-state-deltas there were,
showing only the app-state-deltas from the layer immediately below.  This way even with optimized reticulum mesh connections, an adversarial
host intercepting cannot see how close they are to the actual host of the corsair-app.

Valid-State-Relays (Delta Undercurrent Sources) : A set of Archipelagos or isles which may relay valid pre-aggregated states periodically to
the app being hosted.  If we are downstream from another source app, we may be in their list of valid-state-relays.

Delta-State-Aggregation : A set of isles or archipelagos can collaborate via reticulum to mesh their state before sending it to an
upstream X.509 cert in the reticulum mesh layer.  Each member in this aggregation should be able to use hashing to ensure that their
own deltas were merged into the State-Aggregate before the state-aggregate was relayed.

State-Deltas : Changes applied to an active state in an existing instance of the app, these are aggregated into a single
state-delta before they are propagated to other relays for further aggregation.

It should also keep a record of depricated-states, where the state has occurred too long ago for the app to reasonably maintain
storage for it, making any state-deltas applied in reference to a depricated-state invalid.  However, upon receipt of deprication
based invalid state-delta, there should be a log and a warning sent back to the sender of the invalid delta.