// The product name lives here and in the APP_NAME setting; the server's value wins.
export const DEFAULT_APP_NAME = "CleanSync";
export const CONFIRM_PHRASE = "I understand and want to start synchronization.";
/**
 * Spec §9: every New button and menu is hidden behind this one switch; their code and tests
 * stay. Off in every normal build. Unit tests run with it on (vite.config.ts), and an image
 * built with --build-arg VITE_SHOW_NEW=1 has it on for the New browser tests.
 */
export const SHOW_NEW: boolean = import.meta.env.VITE_SHOW_NEW === "1";
