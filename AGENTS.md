# Repository guidance

- Use Python 3.12 and keep runtime dependencies minimal.
- Each cloud task already has an isolated checkout; use it directly. Do not create a Git worktree unless the user asks.
- Keep project data versioned. Add explicit migration handling before changing an existing schema contract.
- Keep source media read-only. Do not copy user media into project folders by default.
- Keep TTS, selection, alignment, effects, and rendering behind `te.interfaces` ports.
- Run `python -m pytest` after changes. Never commit local projects, media, generated audio, caches, secrets, or renders.
