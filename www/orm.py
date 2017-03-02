#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, logging
import aiomysql

#打印SQL查询语句
def log(sql, args=()):
    logging.info('SQL: %s' % sql)


#创建连接池
#创建一个全局连接池，每个HTTP请求都可以从连接池中直接获取数据库连接
#使用连接池的好处是不必频繁地打开和关闭数据库连接，而是能复用就尽量复用

#连接池由全局变量__pool存储，缺省情况下将编码设置为utf-8，自动提交事务
@asyncio.coroutine
def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool
    __pool = yield from aiomysql.create_pool(
    host = kw.get('host', 'localhost'),
    port = kw.get('port', 3306),
    user = kw['user'],
    password=kw['password'],
    db = kw['db'],
    charset=kw.get('charset', 'utf8'),
    autocommit=kw.get('autocommit', True),
    maxsize=kw.get('maxsize', 10),
    minsize=kw.get('minsize', 1),
    #接收一个event_loop实例
    loop=loop
    )

#封装SQL Select语句为select函数
@asyncio.coroutine
def select(sql, args, size=None):
    log(sql, args)
    global __pool
    with (yield from __pool) as conn:
        cur = yield from conn.cursor(aiomysql.DictCursor)
        yield from cur.execute(sql.replace('?', '%s',), args or ())
        if size:
            rs = yield from cur.fetchmany(size)
        else:
            rs = yield from cur.fetchall()
        yield from cur.close()
        logging.info('rows returned: %s' % len(rs))
        return rs

#封装Insert、Update、Delete
#定义一个通用execute()函数，返回一个整数表示影响的行数
@asyncio.coroutine
def execute(sql, args):
    log(sql)
    with (yield from __pool) as conn:
        try:
            cur = yield from conn.cursor()
            yield from cur.execute(sql.replace('?', '%s'), args)
            affected = cur.rowcount
            yield from cur.close()
        except BaseException as e:
            raise
        return affected

#根据输入的参数生成占位符列表
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    #以','为分隔符，将列表合成字符串
    return (','.join(L))
    

#Field类：负责保存表的字段名和字段类型
class Field(object):
    def __init__(self, name, column_type, primary_key, default):
        self.name = name
        self.column_type = column_type
        self.primary_key = primary_key
        self.default = default
        
    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)
        
#定义衍生Field类，表的不同列的字段类型不一样
#如映射varchar的StringField
class StringField(Field):
    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)
        
class BooleanField(Field):
    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

class IntegerField(Field):
    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

class FloatField(Field):
    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

class TextField(Field):
    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)



#定义Model的元类ModelMetaclass，为一个数据库表映射成一个封装的类做准备
class ModelMetaclass(type):
    def __new__(cls, name, bases, attrs):
        #排除Model类本身
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        #获取table名称
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table: %s)' % (name, tableName))
        #获取所有的Field和主键名
        mappings = dict()
        fields = []
        primaryKey = None
        for k, v in attrs.items():
            if isinstance(v, Field):
                #k是类的一个属性，v是这个属性在数据库中对应的Field列表属性
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v
                if v.primary_key:
                    #找到主键
                    if primaryKey:
                        #如果此时类实例已存在主键，说明主键从复了
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    #否则将此列设为列表的主键
                    primaryKey = k
                else:
                    fields.append(k)
        if not primaryKey:
            raise RuntimeError('Primary key not found.')
        #从类属性中删除Field属性    
        for k in mappings.keys():
            attrs.pop(k)
        #保存除主键外的属性名为：运算出字符串 列表形式
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        #保存属性和列的映射关系
        attrs['__mappings__'] = mappings
        attrs['__table__'] = tableName
        attrs['__primary_key__'] = primaryKey
        #保存除主键外的属性名
        attrs['__fields__'] = fields
        #构造默认的select、insert、update、delete语句
        #``的作用等同于repr()
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ','.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) value (%s)' % (tableName, ','.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (tableName, ','.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)
        
            
            


#定义所有ORM映射的基类Model
class Model(dict, metaclass=ModelMetaclass):
    def __init__(self, **kw):
        super(Model, self).__init__(**kw)
        
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)
    
    def __setattr__(self, key, value):
        self[key]=value

    def getValue(self, key):
        return getattr(self, key, None)
        
    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:
            field = self.__mappings__[key]
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                setattr(self, key, value)
        return value

    @classmethod
    #类方法(可以直接通过类而非对象访问的方法)有变量cls传入，从而可以用cls做一些相关处理
    #当有子类继承时，调用该类方法传入的类变量cls是子类，而非父类
    @asyncio.coroutine
    def findAll(cls, where=None, args=None, **kw):
        '''find object by where clause.'''
        sql = [cls.__select__]

        if where:
            sql.append('where')
            sql.append(where)

        if args is None:
            args = []

        orderBy = kw.get('orderBy', None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)

        limit = kw.get('limit', None)
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):
                sql.append('?')
                args.append('limit')
            elif isinstance(limit, tuple) and len(limit) == 2:
                sql.append('?,?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value: %s' )
        rs = yield from select(' '.join(sql), args)
        return [cls(**r) for r in rs]


    @classmethod
    @asyncio.coroutine
    def findNumber(cls, selectField, where=None, args=None):
        '''find number by select and where.'''
        sql = ['select %s __num__ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = yield from select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0][__num__]

    @classmethod
    @asyncio.coroutine
    def find(cls, pk):
        '''find object by primary key.'''
        rs = yield from select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    
    @asyncio.coroutine
    def save(self):
        args = list(map(self.getValueOrDefault, self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows = yield from execute(self.__insert__, args)
        if rows != 1:
            logging.warn('faild to insert record: affected rows: %s' % rows)

    @asyncio.coroutine
    def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = yield from execute(self.__update__, args)
        if rows != 1:
            logging.warn('faild to update by primary key: affected rows: %s' % rows)

    @asyncio.coroutine
    def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = yield from execute(self.__delete__, args)
        if rows != 1:
            logging.warn('faild to remove by primary key: affected rows: %s' % rows)


if __name__ == '__main__':

    class User(Model):
        #定义类的属性到列的映射
        __table__ = 'users'
    
        id = IntegerField('id', primary_key=True)
        name = StringField('username')
        email = StringField('email')
        password = StringField('password')   
