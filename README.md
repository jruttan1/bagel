# bagel

Bagel is an investment assistant that works through text messages.

A user enters their phone number on the landing page and continues through iMessage. They connect their Wealthsimple account and answer a few short questions about their financial position and how they invest.

Bagel syncs their accounts, holdings, balances, transactions, and portfolio history. It follows relevant market activity and sends a short morning message about what is affecting the portfolio. The user can also text questions at any time.

The backend uses FastAPI, Neon Postgres, `ws-api`, messages.dev, OpenAI, and APScheduler.

Portfolio data and current market information take priority over a user's stated thesis. Onboarding answers and investment preferences are kept as background context and only mentioned when they materially change the answer.

Wealthsimple passwords are not stored. The authenticated session is encrypted before it is saved.

Inspired by [Poke](https://interaction.co) after using it to track my calories over text for three months
