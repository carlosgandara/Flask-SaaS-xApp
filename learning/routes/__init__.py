"""Register all feature routes on the learning blueprint."""
from . import menu, lessons, study, shortcuts
from .course import register_course
from .word_match import register_word_match
from .arcade import register_arcade


def register_routes(blueprint, services, include_home=True):
    menu.register_routes(blueprint,include_home=include_home)
    for module in (lessons, study, shortcuts):
        module.register_routes(blueprint)
    register_course(blueprint, services)
    register_word_match(blueprint, services)
    register_arcade(blueprint, services)
