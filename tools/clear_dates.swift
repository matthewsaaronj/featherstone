// Clears due dates and alarms from open reminders in one Reminders list.
// Usage: clear_dates "Featherstone - Backlog" [--dry-run]
import EventKit
import Foundation

let args = CommandLine.arguments
guard args.count >= 2 else { print("usage: clear_dates <list name> [--dry-run]"); exit(2) }
let listName = args[1]
let dryRun = args.contains("--dry-run")
let store = EKEventStore()
let sem = DispatchSemaphore(value: 0)
var granted = false
store.requestFullAccessToReminders { ok, err in
    granted = ok
    if let err = err { print("access error: \(err)") }
    sem.signal()
}
sem.wait()
guard granted else { print("no Reminders access"); exit(1) }

guard let cal = store.calendars(for: .reminder).first(where: { $0.title == listName }) else {
    print("list not found: \(listName)"); exit(1)
}
let pred = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: [cal])
var items: [EKReminder] = []
store.fetchReminders(matching: pred) { rs in items = rs ?? []; sem.signal() }
sem.wait()

var cleared = 0
for r in items where r.dueDateComponents != nil || r.hasAlarms {
    print("\(dryRun ? "would clear" : "clearing"): \(r.title ?? "?")")
    if !dryRun {
        r.dueDateComponents = nil
        r.startDateComponents = nil
        if let alarms = r.alarms { for a in alarms { r.removeAlarm(a) } }
        do { try store.save(r, commit: false); cleared += 1 } catch { print("save error: \(error)") }
    }
}
if !dryRun { try? store.commit() }
print("done: \(cleared) cleared, \(items.count) open in \(listName)")
