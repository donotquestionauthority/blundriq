"""Game import: Chess.com and Lichess → chess_games + player_games.

`chesscom.py` and `lichess.py` fetch and parse into `GameRecord`s; `store.py`
writes them; `run.py` is the step the pipeline runs. Only this package writes
`chess_games` (tests/test_import.py enforces it).
"""
