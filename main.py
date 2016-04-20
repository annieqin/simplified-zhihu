# coding: utf-8

import datetime
import json
import decimal

from peewee import *
import pymongo
import tornado.web
import tornado.ioloop
import os.path
from bson import ObjectId

# from lib.json_utils import LazableJSONEncoder

APPLY_FRIEND = 1
ANSWER_QUESTION = 2
SYSTEM_MESSAGE = 3

UNPROCESSED = 0
PROCESSED = 1

mysql_db = MySQLDatabase('msgboard', host='127.0.0.1', port=3306, user='root')

mongo_conn = pymongo.MongoClient('localhost', 27017)
mongo_db = mongo_conn['msgboard']
# collection = mongo_db['questions']
# collection.insert({'d': 'test'})


def is_aware(value):
    """
    Determines if a given datetime.datetime is aware.
    The logic is described in Python's docs:
    http://docs.python.org/library/datetime.html#datetime.tzinfo
    """
    return value.tzinfo is not None and value.tzinfo.utcoffset(value) is not None


class LazableJSONEncoder(json.JSONEncoder):
    """
    JSON serializable datetime.datetime
    """
    def default(self, o):
        if isinstance(o, datetime.datetime):
            r = o.isoformat()
            if o.microsecond:
                r = r[:23] + r[26:]
            if r.endswith('+00:00'):
                r = r[:-6] + 'Z'
            return r
        elif isinstance(o, datetime.date):
            return o.isoformat()
        elif isinstance(o, datetime.time):
            if is_aware(o):
                raise ValueError('JSON can\'t represent timezone-aware times.')
            r = o.isoformat()
            if o.microsecond:
                r = r[:12]
            return r
        elif isinstance(o, decimal.Decimal):
            return str(o)
        else:
            return super(LazableJSONEncoder, self).default(o)


class BaseModel(Model):
    class Meta:
        database = mysql_db


class Message(BaseModel):
    PUBLIC = 1
    PRIVATE = 2
    DELETED = 0

    STATUS_CHOICES = (
        (PUBLIC, '公开'),
        (PRIVATE, '私密'),
        (DELETED, '已删除'),
    )
    author = CharField(max_length=15, default='')
    content = TextField(default='')
    status = IntegerField(choices=STATUS_CHOICES, default=PUBLIC)
    created_at = DateTimeField(default=datetime.datetime.now)
    updated_at = DateTimeField(null=True, default=None)


class UserRelation(BaseModel):
    NOT_ADDED = 1
    APPLYING = 2
    APPLIED = 3
    ADDED = 4
    REJECTING = 5  # 可删除
    REJECTED = 6  # 可删除
    DELETED = 7  # 可删除

    STATUS_CHOICES = (
        (NOT_ADDED, '未添加'),  # 显示加为好友
        (APPLYING, '请求加对方为好友'),  # 显示等待对方处理您的好友请求
        (APPLIED, '对方请求加你为好友'),  # 显示请处理对方的好友请求
        (ADDED, '已添加'),  # 显示删除好友
        (REJECTING, '拒绝加对方为好友'),
        (REJECTED, '对方拒绝加你为好友'),
        (DELETED, '已删除'),
    )
    user = CharField(max_length=15, default='')
    friend = CharField(max_length=15, default='')
    status = IntegerField(choices=STATUS_CHOICES, default=NOT_ADDED)


class User(BaseModel):
    login = CharField(max_length=15, default='')
    pwd = CharField(max_length=255, default='')


def create_tables():
    mysql_db.connect()
    mysql_db.create_tables([Message, User, UserRelation], safe=True)


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie('login')

    def return_json(self, data):
        self.write(json.dumps(data, cls=LazableJSONEncoder))
        self.set_header('Content-Type', 'application/json; charset=UTF-8')

    def return_status(self, status=599, message=''):
        self.write({
            'status': status,
            'message': message
        })


class MainHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        data = {
            'query': Message.select().where(Message.status != Message.DELETED),
            'user': self.current_user
        }
        self.render('home.html', **data)

    @tornado.web.authenticated
    def post(self):
        author = self.current_user
        content = self.get_argument('content')
        status = self.get_argument('status')

        Message.create(author=author,
                       content=content,
                       status=status)
        # 不用写返回也可以 默认成功？


class RegisterHandler(BaseHandler):
    def get(self):
        self.render('register.html')

    def post(self):
        login = self.get_argument('login')
        pwd = self.get_argument('pwd')
        pwd_confirm = self.get_argument('pwd-confirm')
        if pwd == pwd_confirm:
            User.create(login=login,
                        pwd=pwd)
            self.redirect('/')
        else:
            self.redirect('/register')


class LoginHandler(BaseHandler):
    def get(self):
        self.render('login.html')

    def post(self):
        login = self.get_argument('login')
        pwd = self.get_argument('pwd')

        self.set_secure_cookie('login', login)

        try:
            user = User.get(User.login == login, User.pwd == pwd)
            if user:
                self.redirect('/')
            else:
                self.redirect('/login')
        except User.DoesNotExist:
            self.redirect('/login')


class LogoutHandler(BaseHandler):
    def get(self):
        self.clear_cookie("login")
        self.redirect('/login')


class FriendsListHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        data = {
            'query': UserRelation.select().where(
                (UserRelation.user == self.current_user) |
                (UserRelation.friend == self.current_user),
                UserRelation.status == UserRelation.ADDED,
            ),
            'user': self.current_user
        }
        self.render('friends.html', **data)


class SearchUserHandler(BaseHandler):
    def get(self, search_user):
        user = self.current_user
        relation_status = None
        try:
            s_user = User.get(User.login == search_user)
            if s_user:
                if s_user.login == user:
                    self.write('你不能加自己为好友')
                    return
                relation = None
                try:
                    relation = UserRelation.get(
                        (UserRelation.user == user) | (UserRelation.user == search_user),
                        (UserRelation.friend == user) | (UserRelation.friend == search_user)
                    )
                except (UserRelation.DoesNotExist):
                    relation_status = UserRelation.NOT_ADDED

                self.write({
                    'id': s_user.id,
                    'login': s_user.login,
                    'relation_status': relation_status or relation.status
                })
            else:
                self.write('不存sss在此用户')

        except (User.DoesNotExist):
            self.write('不存在此用户')


class UserHandler(BaseHandler):
    def get(self, user):
        try:
            user = User.get(User.login == user)
            if user:
                self.write(user.login)
            else:
                pass
        except User.DoesNotExist:
            self.write('不存在此用户')


class ApplyFriendHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        from_user = self.current_user
        to_user = self.get_argument('to_user')

        relation = UserRelation.select().where(
            (UserRelation.user == from_user) | (UserRelation.user == to_user),
            (UserRelation.friend == from_user) | (UserRelation.friend == to_user)
        ).exists()
        if relation:
            self.write('双方已是好友或双方有未处理的好友请求')
            return
        UserRelation.create(user=from_user,
                            friend=to_user,
                            status=UserRelation.APPLYING)

        mongo_db.messages.insert({
            'to_user': to_user,
            'from_user': from_user,
            'type': APPLY_FRIEND,
            'status': UNPROCESSED,
            'created_at': datetime.datetime.now()
        })


class DealFriendApplyingHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        from_user = self.get_argument('from_user')
        to_user = self.current_user
        flag = self.get_argument('flag')
        message_id = self.get_argument('message_id')
        relation = None

        if flag in ('0', '1'):
            try:
                relation = UserRelation.get(UserRelation.user == from_user,
                                            UserRelation.friend == to_user,
                                            UserRelation.status == UserRelation.APPLYING)
            except UserRelation.DoesNotExist:
                pass
            if flag == '1':
                relation.status = UserRelation.ADDED
                relation.save()

                mongo_db.messages.update({
                    '_id': ObjectId(message_id)
                }, {
                    '$set': {
                        'status': PROCESSED
                    }
                })
            elif flag == '0':
                relation.status = UserRelation.REJECTED
                relation.save()
        else:
            pass


class MessagesListHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        cursor = mongo_db.messages.find({
            'to_user': self.current_user
        })
        cursor_list = list(cursor)

        ret = []
        for doc in cursor_list:
            question_id = question_title = question_url = None
            if doc.get('question_id'):
                question = list(mongo_db.questions.find({'_id': ObjectId(doc.get('question_id'))}))[0]
                question_id = str(doc.get('question_id'))
                question_title = question['title']
                question_url = question['url']
            ret.append({
                'message_id': str(doc['_id']),
                'from_user': doc['from_user'],
                'created_at': doc['created_at'],
                'type': doc['type'],
                'status': doc['status'],
                'content': doc.get('content'),
                'question_id': question_id,
                'question_title': question_title,
                'question_url': question_url,
            })
        data = {
            'ret': ret,
            'user': self.current_user
        }
        self.render('messages.html', **data)


class QuestionsListHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        current_user = self.current_user
        query = UserRelation.select().where(
            UserRelation.status == UserRelation.ADDED,
            (UserRelation.user == current_user) | (UserRelation.friend == current_user)
        )
        friends = [i.user if i.user != current_user else i.friend for i in query]
        friends.append(current_user)
        print friends

        cursor = mongo_db.questions.find({
            'user': {
                '$in': friends
            }
        })
        cursor_list = list(cursor)
        ret = [{
            'user': doc['user'],
            'question_id': str(doc['_id']),
            'question_title': doc['title'],
            'question_url': doc['url'],
            'answers_count': doc['answers_count'],
            'created_at': doc['created_at'],
        } for doc in cursor_list]

        data = {
            'ret': ret,
            'user': self.current_user
        }
        self.render('questions.html', **data)


class AskQuestionHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        question_title = self.get_argument('question_title')
        question_description = self.get_argument('question_description')

        question_id = mongo_db.questions.insert({
            'title': question_title,
            'description': question_description,
            'created_at': datetime.datetime.now(),
            'user': self.current_user,
            'answers_count': 0
        })

        question_url = '/question/' + str(question_id)
        mongo_db.questions.update({
            '_id': question_id
        }, {
            '$set': {
                'url': question_url
            }
        })

        self.return_json({
            'question_url': question_url,
            'question_title': question_title,
            'created_at': datetime.datetime.now(),
            'answers_count': 0,
            'user': self.current_user
        })


class QuestionHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, question_id):
        cursor = list(mongo_db.questions.find({
            '_id': ObjectId(question_id)
        }))[0]

        data = {
            'question_id': question_id,
            'ret': cursor
        }
        self.render('question.html', **data)


class AnswerQuestionHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        question_id = self.get_argument('question_id')
        content = self.get_argument('answer')
        to_user = self.get_argument('question_from')
        from_user = self.current_user

        mongo_db.questions.update({
            '_id': ObjectId(question_id)
        }, {
            '$push': {
                'answers': {
                    'content': content,
                    'from_user': from_user,
                    'created_at': datetime.datetime.now()
                },
            },
            '$inc': {
                'answers_count': 1
            }
        })

        mongo_db.messages.insert({
            'to_user': to_user,
            'from_user': from_user,
            'status': UNPROCESSED,
            'type': ANSWER_QUESTION,
            'created_at': datetime.datetime.now(),
            'question_id': question_id
        })

        self.return_json({
            'content': content,
            'from_user': from_user,
            'created_at': datetime.datetime.now()
        })


settings = {
    'static_path': os.path.join(os.path.dirname(__file__), 'static'),
    'template_path': os.path.join(os.path.dirname(__file__), 'templates'),
    'cookie_secret': 'B/Gan68MRn+1BKVD5NOxn55P3Vgy/UlnlEcNaDM0rQI=',
    # 'xsrf_cookies': True,
    'login_url': '/login',
}

application = tornado.web.Application([
    (r'/', MainHandler),
    (r'/register', RegisterHandler),
    (r'/login', LoginHandler),
    (r'/logout', LogoutHandler),
    (r'/friends', FriendsListHandler),
    (r'/search_user/(?P<search_user>[0-9a-z]+)', SearchUserHandler),
    (r'/user/(?P<user>[0-9a-z]+)', UserHandler),
    (r'/apply_friend', ApplyFriendHandler),
    (r'/deal_friend', DealFriendApplyingHandler),
    (r'/messages', MessagesListHandler),
    (r'/questions', QuestionsListHandler),
    (r'/ask_question', AskQuestionHandler),
    (r'/question/(?P<question_id>[0-9a-z]+)', QuestionHandler),
    (r'/answer_question', AnswerQuestionHandler)
], **settings)


if __name__ == '__main__':
    application.listen(1988)
    tornado.ioloop.IOLoop.current().start()