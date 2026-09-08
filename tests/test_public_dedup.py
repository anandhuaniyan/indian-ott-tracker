from datetime import date
from types import SimpleNamespace as NS
from app.api.v1.public import movie_identity, _cast_payload


def test_identity_never_uses_platform_and_prefers_strong_ids():
    def movie(**kw):
        return NS(**(dict(id=1,tmdb_id=None,external_ids=[],title='Aroopi!',release_date=date(2026,1,1),original_language='ml')|kw))
    assert movie_identity(movie(tmdb_id=20)) == ('tmdb',20)
    assert movie_identity(movie(external_ids=[NS(provider='IMDb',external_id='tt1234567')])) == ('imdb','tt1234567')
    assert movie_identity(movie()) == movie_identity(movie(id=2,title='AROOPI'))
    assert movie_identity(movie()) != movie_identity(movie(original_language='ta'))
    assert movie_identity(movie(tmdb_id=20)) != movie_identity(movie(tmdb_id=21))


def test_cast_groups_person_and_preserves_distinct_roles():
    person=NS(id=1,tmdb_id=100,imdb_id=None,name='Actor',profile_path=None)
    credits=[NS(person=person,credit_type='cast',character=c,cast_order=o) for c,o in [('Hero',2),('Hero',3),('Father',4)]]
    result=_cast_payload(credits)
    assert len(result)==1
    assert result[0]['character']=='Hero / Father'
    assert result[0]['order']==2
