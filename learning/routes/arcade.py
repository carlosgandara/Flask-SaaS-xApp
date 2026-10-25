"""Free arcade games, independent of lesson access and completion."""
from flask import jsonify, render_template, request, abort


def register_arcade(bp, services):
    def selection(number):
        levels = services['get_levels']()['levels']
        if not 1 <= number <= len(levels):
            abort(404)
        ids = services['content_store'].get_curriculum().get(levels[number - 1]['id'], [])
        catalog = services['dose_catalog']()
        if len(ids) != 10 or any(cid not in catalog for cid in ids):
            abort(503)
        return levels, [catalog[cid] for cid in ids]

    @bp.get('/games/word-shooter')
    def arcade_shooter():
        try:
            number = int(request.args.get('level', 1))
        except ValueError:
            abort(400)
        levels, words = selection(number)
        progress = services['get_progress']()
        history = progress.get('games', {}).get('arcade_shooter', {})
        game_words = []
        for word in words:
            item = history.get(word['id'], {})
            game_words.append(dict(id=word['id'], answer=word['tagalog'], english=word['english'],
                                   correct=item.get('correct', 0), wrong=item.get('wrong', 0),
                                   recall_streak=0, mastered=False))
        return render_template('learning/word-shooter.html', game_words=game_words,
                               shooter_levels=[{'mastered':0, 'total':10} for _ in levels],
                               shooter_level=number, shooter_mastery=3, arcade=True)

    @bp.post('/games/word-shooter/answer')
    def arcade_shooter_answer():
        data = request.get_json(silent=True)
        if not isinstance(data, dict) or type(data.get('level')) is not int:
            abort(400)
        _, words = selection(data['level'])
        word = next((word for word in words if word['id'] == data.get('word_id')), None)
        if word is None or not isinstance(data.get('answer'), str) or len(data['answer']) > 120:
            abort(400)
        correct = services['dose_normalize'](data['answer']) in {
            services['dose_normalize'](answer) for answer in word['accepted_answers']}
        with services['progress_lock']:
            progress = services['get_progress']()
            history = progress.setdefault('games', {}).setdefault('arcade_shooter', {})
            item = history.setdefault(word['id'], {'correct':0, 'wrong':0})
            item['correct' if correct else 'wrong'] += 1
            services['save_progress'](progress)
        # Round mastery belongs to the game client; only cumulative game history is saved.
        # Arcade practice never grants course mastery or unlocks lessons.
        return jsonify(ok=True, correct=correct, stats={'correct':item['correct'], 'wrong':item['wrong']},
                       level_complete=False)

