# Unsubscribe

**Mail → CleanSync → Unsubscribe** lists the senders whose mail has an unsubscribe link, and helps you stop the ones you don't want. It works on the mailbox picked in the account switcher.

## What the page lists

One row per sender, for every sender that has an unsubscribe link in its mail: how many mails, how many of them you read, how many are in your Inbox, when the last one came, how much space they take, and what you've already done about them.

The tabs:

- **Suggested**: senders with at least 3 mails where you read fewer than one in five, and that you haven't dealt with yet.
- **All**: every sender with an unsubscribe link.
- **Unsubscribed**, **Kept** and **Auto-archived**: the senders you've dealt with, by what you did.

You can search senders, sort them, and pick one, several or all of a list. Old mail is scanned once in the background the first time; the page says "Scanning old mail" until it is done, and you can use it meanwhile. Mail that arrives later is read as it is backed up.

## What Unsubscribe tries, in order

For each sender, CleanSync looks at the unsubscribe link in their newest mail and tries:

1. **The one-click request.** Only when the sender's mail says it takes one: a plain "unsubscribe me" request sent to the link in the mail's header. If they answer yes, that's it. If they send CleanSync on to another page instead, it goes to step 2. A link found only in the mail's text never gets this request.
2. **Opening their link.** If the page is not a form, CleanSync counts the visit as the unsubscribe.
3. **A simple form.** If the page has one form with no password or file fields and no captcha, CleanSync sends it with what the page already filled in (for example your address in an email box), then has your model read the page that comes back to check it says you're unsubscribed. It only ticks a box whose name or value is the whole word "unsubscribe", "unsub" or "all", and never one that talks about allowing, subscribing, marketing, offers or partners. A form that needs more than this is left to you.
4. **A mail address only.** If the only way is to write to an address, CleanSync does not write for you (see below). With Allow Gmail filters on, it makes a filter so their new mail skips your Inbox. Otherwise it says **Needs you** and gives you a **Write to it** link.

Each sender ends with one of these:

- **Unsubscribed**: the sender said yes.
- **Auto-archived**: the sender only gave a mail address, so with Allow Gmail filters on CleanSync made a filter that keeps their new mail out of your Inbox.
- **Couldn't confirm**: CleanSync tried but the page didn't say you were unsubscribed, or it was a form CleanSync won't fill in, or there was no model to check the page. It gives you an **Open page** link to finish in your browser.
- **Needs you**: only a mail address was available and filters are off, or a filter couldn't be made (for example Gmail's limit of 1,000 filters). Use **Write to it**, or turn on Allow Gmail filters.

When you've finished one yourself, press **Mark unsubscribed**.

## What CleanSync never does

- **It never sends mail.** Not an unsubscribe mail, not a reply. If the only way is to write to an address, you do that.
- **It refuses private addresses.** It only opens ordinary web links on the usual web ports, and refuses links that point at your own network or this computer, so a mail can't make CleanSync poke at things inside your home or office. Redirects are followed a few times and checked the same way.

## Auto-archive and Keep

- **Auto-archive** makes a Gmail filter that skips the Inbox for that sender's new mail (they are still in Gmail, under All mail). It needs Allow Gmail filters.
- **Keep** says "don't suggest this sender again". If the sender was auto-archived, Keep removes CleanSync's filter for it, which also needs Allow Gmail filters.

You can do either from a row's menu, or for many senders at once from the bar that shows when you pick some. The same menu has **Open their mail**, **Offload their mail** and (with Allow permanent delete on) **Delete forever their mail**. Offload and Delete forever work on one sender at a time, so from the bar they show only when exactly one sender is picked. You can pick at most 5,000 senders in one go.

## Allow Gmail filters

Off at first, per mailbox. **Settings → Accounts → Allow Gmail filters → Turn on** asks Google for one more permission for that mailbox, `gmail.settings.basic`. It lets CleanSync create and delete Gmail filters, and nothing else it didn't already have: it can't send mail, and it only touches filters it makes for Auto-archive and Unsubscribe. Turn it off and CleanSync stops making filters; the permission stays at Google until you remove it at myaccount.google.com. See [Setting up Google](google-oauth-setup.md) for adding the permission to your Google client.

## The Unsubscribe model task

Checking a form's reply uses a model. **Settings → Models** has an **Unsubscribe** task for it. If you leave it unset it uses the Sorting model. With no model, forms end as **Couldn't confirm** (the other steps don't need one). The model only reads the reply page's text to say whether you're unsubscribed; it can't click or send anything.

## Gmail filters only affect new mail

A filter keeps a sender's mail that arrives after it is made out of your Inbox. It does nothing about the mail already there. To clear that out, use **Offload their mail** on the same row (see [Mail backup](mail-backup.md#offloading-mail)).
