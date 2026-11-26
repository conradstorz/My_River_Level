# River Monitor

Watches river gauges and tells people, on their own messaging channel, when the
rivers they care about become unusual or start to flood.

## Language

**Page**:
A named set of watched gauges together with its **Subscribers**.
_Avoid_: Landing page, user page

**Pin page**:
A **Page** created by dropping a pin on a map, owned by one Telegram chat.
_Avoid_: Map page

**Subscriber**:
A person attached to a **Page** who receives its real alerts.
_Avoid_: Recipient, user, member

**Broadcast list**:
The global list of contacts who receive only manual broadcasts from the admin, never alerts.
_Avoid_: Subscribers, global subscribers
