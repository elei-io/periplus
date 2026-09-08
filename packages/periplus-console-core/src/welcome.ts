const WORDMARK = [
  " ____  _____ ____  ___ ____  _    _   _ ____  ",
  "|  _ \\| ____|  _ \\|_ _|  _ \\| |  | | | / ___| ",
  "| |_) |  _| | |_) || || |_) | |  | | | \\___ \\ ",
  "|  __/| |___|  _ < | ||  __/| |__| |_| |___) |",
  "|_|   |_____|_| \\_\\___|_|   |_____\\___/|____/ ",
  "",
].join("\n")

const HELP = "Type .help for commands. Press Tab for SQL autocomplete."

export const WELCOME = [
  WORDMARK,
  "If you could query the web with SQL, what would you ask it?",
  HELP,
].join("\n")

export const ADMIN_WELCOME = [WORDMARK, HELP].join("\n")
