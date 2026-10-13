/** "1 mail", "1,284 mails". A leaf module: it imports nothing, so any file may use it. */
export const mails = (n: number) => `${n.toLocaleString()} ${n === 1 ? "mail" : "mails"}`;
