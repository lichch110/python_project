#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import orm, asyncio
from models import User, Blog, Comment

@asyncio.coroutine
def test(loop):
    yield from orm.create_pool(loop=loop, user='awe_user', password='awe_passwd', db='awesome')
    u = User(name='Test1', email='test1@example.com', passwd='1234567890', image='about:blank')
    yield from u.save()

loop = asyncio.get_event_loop()
loop.run_until_complete(test(loop))
loop.close()
