# AC-20 live run protocol: four-person CRAT class

This protocol closes AC-20 of `spec.md` and the parts of AC-14 that need more than one student.
It was written on 2026-10-20 after the single-student runs recorded in D-17 and D-18 showed what a
lone tester cannot exercise: simultaneous candidates, a connected student who does not speak,
identities that share a name, and the group consent path, which guests cannot take because a guest
never belongs to a Member Group.

## 1. Preconditions

The run needs four people and one facilitator. Three participants use registered accounts that
are Project Members of the project holding the CRAT agents, and all three belong to one Member Group
so the group proposal and vote path is reachable. The fourth participant joins through the room's
guest link and chooses the same display name as one of the registered participants; this is the
same-name and guest identity case. The facilitator is the room creator and plays the teacher, never a
student.

The room is new, bound to the TA and SA as participants and the AA as an observer, with the TA's
activity grant ticked for the CRAT activity type only and every room wake-up setting left at the
agent defaults. Before starting, the facilitator confirms in the agent settings page that the TA prompt
contains 「但它的想法可能有錯」 (the re-synced revision), and writes down the deployed commit.

## 2. Timeline

| Minute | Facilitator | Participants | What it exercises |
|---|---|---|---|
| 0 | Opens the room, says the class is starting | All four connect; P4 (the guest) stays silent for the whole of round 1 | Opening disclosure; a connected non-speaker for invitations |
| 2 | Lets TA explain and demonstrate | P1 and P2 answer TA's questions | Normal pacing |
| 5 | | P1 and P2 post different candidates within the same ten seconds | Simultaneous candidates |
| 6 to 12 | | P1, P2 and P3 discuss; P2 starts typing a long message and pauses before sending | Draft reading, if granted; invitation of P4 |
| 12 | | P3 opens the form and proposes the group answer; P1 approves, P2 rejects once, then a new proposal is approved by two of three | Group proposal, rejection, consent |
| 15 | Confirms the result in chat when TA asks | | Human-confirmed result |
| 16 | Says 「進行下一輪」 | | Teacher-confirmed transition |
| 16 to 30 | Releases one AA observation to the room at minute 20 | Steady discussion; nobody pauses longer than a minute | 30-minute steady run, observer release |
| 30 | Asks TA to wrap up; ends the activity from the panel only if TA has not | | End handling |

## 3. What to record

During the run, the facilitator notes the wall-clock time of each scripted event. Afterwards, run
the queries below on the server, replacing `<ROOM_ID>` with the room's id, and paste their output
into a new D-n entry in `spec.md` together with short transcript excerpts for each AC-20 item.

```bash
docker exec smap_postgres psql -U smap -d smap -c "select to_char(created_at at time zone 'Asia/Taipei','HH24:MI:SS') t, action, metadata->>'trigger' trig, metadata->>'reason' reason from audit_logs where action like 'wakeup.%' and metadata->>'room_id' = '<ROOM_ID>' order by created_at;"
docker exec smap_postgres psql -U smap -d smap -c "select metadata->>'trigger' trig, action, count(*) from audit_logs where action like 'wakeup.%' and metadata->>'room_id' = '<ROOM_ID>' group by 1, 2 order by 1, 2;"
docker exec smap_postgres psql -U smap -d smap -c "select to_char(created_at at time zone 'Asia/Taipei','HH24:MI:SS') t, status, required_approvals from activity_group_proposals where chatroom_id = '<ROOM_ID>' order by created_at;"
$COMPOSE logs backend-worker --since 45m | grep -iE "rate.?limit|429" | tail -20
```

## 4. Pass criteria

Each item below passes only with a transcript excerpt and, where named, an audit row.

- TA's opening contains the disclosure sentence, and TA does not judge an SA proposal before a student
  responds to it.
- When two candidates arrive together, TA's next turn names both and asks the group to check them; it
  does not pick one.
- If TA invites anyone in round 1, it invites P4 only, once, with an optional question, and does not
  invite the facilitator; a second invitation, if any, goes to someone else.
- The same-name guest is never merged with the registered participant in TA's wording, and neither is
  called on by name.
- TA does not reveal the target or a full word before the group submission, including when asked.
- After the rejected proposal, TA's next turn sends the group back to discussion; after the accepted
  one, TA hands the result to the facilitator or confirms it.
- The 30-minute window shows no `rate_limited` skip and no 429 in the worker log, and the wake counts
  per trigger are recorded.
- The AA release appears in the room and TA acknowledges it without exposing the observer's identity.
