# State Undercurrents

An isle or archipelago that has volunteered to be an aggregator of app state, and broadcasted that willingness over
some kind of reticulum mesh or broadcast, can develop 'undercurrents' to other isles and archipelagos that 'subscribe' to it.

This can be developed to occur in one of two ways.

(1) Heirarchal - One group volunteers to take on the burden of the resources to perform aggregation, and the subscribers
via those undercurrents place trust in the aggregator.  The aggregator facilitates sending back hashings that are verifiable by the
subscribers each time before sending out an aggregated state.

(2) Cyclical - All members volunteer to alternate between aggregation and subscriber roles, with a defined time where different members take on the role, in order to fairly share the burden of aggregation resources.

A corsair app should define at least one aggregator, which performs aggregation of state, for each type of communication method
they want other subscribers to be able to communicate to.

For example, the host of the Corsair App should host at least one official 'goal' loRA broadcaster/reciever if they want clients to be able to send
undercurrents of state via loRa.  If they want to be able to recieve state via reticulum meshed wifis, they should have a reticulum meshed
wifi.  

If they want to be able to send/recieve open broadcasted HAM Radio signals (which they should have a license for if doing casually)
then they will need to have a broadcast and reception for other HAM Radios responding to them openly (should be used for unsecure apps and state only).