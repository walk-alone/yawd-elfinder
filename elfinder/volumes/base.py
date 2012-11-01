import os, datetime, mimetypes, re, inspect, time  
from PIL import Image
from base64 import b64encode, b64decode
from string import maketrans
from tarfile import TarFile
from django.core.cache import cache
from django.utils.translation import ugettext as _
from elfinder.exceptions import ElfinderErrorMessages, FileNotFoundError, DirNotFoundError, PermissionDeniedError, NamedError, NotAnImageError
from elfinder.utils.archivers import ZipFileArchiver

class ElfinderVolumeDriver(object):
    """
    The base volume finder. Every elfinder volume driver should subclass this.
    """
    
    #The driver id.
    #Must started with a letter and contain only [a-z0-9]
    #Used as part of volume id
    _driver_id = 'a'
    
    #Directory separator - required by the client
    _separator = os.sep
    
    #*********************************************************************#
    #*                            INITIALIZATION                         *#
    #*********************************************************************#
    
    def __init__(self):
        """
        Default constructor
        """

        #Volume id - used as prefix for files hashes
        self._id = ''
        #Flag - volume "mounted" and available
        self._mounted = False
        #Root directory path
        self._root = ''
        #Root basename | alias
        self._root_name = ''
        #Default directory to open
        self._start_path = ''
        #Store moved  or overwrited files info
        self._removed = []
        #Is thumbnails dir writable
        self._tmb_path_writable = False
        #Today 24:00 timestamp
        self._today = 0
        #Yesterday 24:00 timestamp
        self._yesterday = 0
        #list of attributes
        self._attributes = []
        #Default permissions
        self._defaults = {}
        #Archivers config
        self._archivers = {
            'create' : {},
            'extract' : {}
        }
        
        #Object configuration
        self._options = {
            'id' : '',
            #root directory path
            'path' : '/',
            #alias to replace root dir_ name
            'alias' : '',
            #root url, not set to disable sending URL to client (replacement for old "fileURL" option)
            'URL' : '',
            #open this path on initial request instead of root path
            'startPath' : '',
            #how many subdirs levels return per request
            'treeDeep' : 1,
            #directory separator. required by client to show paths correctly
            'separator' : os.sep,
            #directory for thumbnails
            'tmbPath' : '.tmb',
            #mode to create thumbnails dir
            'tmbPathMode' : 0777,
            #thumbnails dir URL. Set it if store thumbnails outside root directory
            'tmbURL' : '',
            #thumbnails size (px)
            'tmbSize' : 48,
            #thumbnails crop (True - crop, False - scale image to fit thumbnail size)
            'tmbCrop' : True,
            #thumbnails background color (hex #rrggbb or 'transparent')
            'tmbBgColor' : '#ffffff',
            #on paste file -  if True - old file will be replaced with new one, if False new file get name - original_name-number.ext
            'copyOverwrite' : True,
            #if True - join new and old directories content on paste
            'copyJoin' : True,
            #on upload -  if True - old file will be replaced with new one, if False new file get name - original_name-number.ext
            'uploadOverwrite' : True,
            #filter mime types to allow
            'onlyMimes' : [],
            #mimetypes allowed to upload
            'uploadAllow' : [],
            #mimetypes not allowed to upload
            'uploadDeny' : [],
            #order to proccess uploadAllow and uploadDeny options
            'uploadOrder' : ['deny', 'allow'],
            #maximum upload file size. Set as number or string with unit - "10M", "500K", "1G". NOTE - applies to each uploaded file individually
            'uploadMaxSize' : 0,
            #files dates format. CURRENTLY NOT IMPLEMENTED
            'dateFormat' : 'j M Y H:i',
            #files time format. CURRENTLY NOT IMPLEMENTED
            'timeFormat' : 'H:i',
            #if True - every folder will be check for children folders, otherwise all folders will be marked as having subfolders
            'checkSubfolders' : True,
            #allow to copy from this volume to other ones?
            'copyFrom' : True,
            #allow to copy from other volumes to this one?
            'copyTo' : True,
            #list of commands disabled on this root
            'disabled' : [],
            #regexp or function name to validate new file name
            'acceptedName' : r'^[^\.].*', #<-- DONT touch this! Use constructor options to overwrite it!
            #callable to control file permissions
            'accessControl' : None,
            #default permissions. Do not set hidden/locked here - take no effect
            'defaults' : {
                'read' : True,
                'write' : True
            },
            #a list of dictionaries, each defining a 'pattern' and values for at
            #least one of the 'hidden', 'locked', 'read' and 'write' attributes for this pattern
            'attributes' : [],
            #Allowed archive's mimetypes to create. Leave empty for all available types.
            'archiveMimes' : [],
            #Manual config for archivers.
            'archivers' : {},
            #max allowed archive files size (0 - no limit)
            'maxArchiveSize' : 0
        }
                
    #*********************************************************************#
    #*                              PUBLIC API                           *#
    #*********************************************************************#
    
    def name(self):
        """
        Return driver name.
        """
        return self.__class__.__name__[len('elfindervolume'):].lower()
    
    def driver_id(self):
        """
        Return driver id. Used as a part of volume id.
        """
        return self._driver_id
    
    def id(self):
        """
        Return volume id
        """
        return self._id
        
    def debug(self):
        """
        Return debug info for client
        """
        return {
            'id' : self.id(),
            'name' : self.name(),
        }
    
    def mount(self, opts):
        """
        "Mount" volume. Return True if volume available for read or write,
        False otherwise.
        """
        
        self._options.update(opts)

        if self._options['id']:
            self._id = '%s%s_' % (self._driver_id, self._options['id'])
        else:
            raise Exception(_('No volume id found'))
        
        self._root = self._normpath(self._options['path'])
        self._separator = self._options['separator'] if 'separator' in self._options else os.sep

        #default file attribute
        self._defaults = {
            'read' : self._options['defaults']['read'] if 'read' in self._options['defaults'] else True,
            'write' : self._options['defaults']['write'] if 'write' in self._options['defaults'] else True,
            'locked' : False,
            'hidden' : False
        }

        #root attributes
        self._attributes.insert(0, {
            'pattern' : '^%s$' % re.escape(self._separator),
            'locked' : True,
            'hidden' : False
        })

        #set files attributes
        for a in self._options['attributes']:
            #attributes must contain pattern and at least one rule
            if 'pattern' in a and len(a) > 1:
                self._attributes.append(a)

        #assign some options to private members
        self._today = time.mktime(datetime.date.today().timetuple())
        self._yesterday = self._today-86400
        
        #set uploadMaxSize  
        try:
            units = {
                'k' : 1024,
                'm' : 1048576,
                'g' : 1073741824,
                'b' : 1
            }        
            self._options['uploadMaxSize'] = int(self._options['uploadMaxSize'][:-1]) * units[self._options['uploadMaxSize'][-1].lower()]
        except (TypeError, KeyError):
            self._options['uploadMaxSize'] = 0
        
        self._root_name = self._basename(self._root) if not self._options['alias'] else self._options['alias']
        
        try:
            root = self.stat(self._root)
        except os.error:
            raise DirNotFoundError

        if (not 'read' in root or not root['read']) and (not 'write' in root and not root['write']):
            raise PermissionDeniedError
        
        if 'read' in root and root['read']:
            #check startPath - path to open by default instead of root
            if self._options['startPath']:
                try:
                    startpath = self._join_path(self._root, self._options['startPath'])
                    start = self.stat(startpath)
                    if start['mime'] == 'directory' and start['read'] and not self._is_hidden(start):
                        self._start_path = self._normpath(startpath)
                except os.error:
                    #Fail silently if startPath does not exist
                    pass
        else:
            self._options['URL'] = ''
            self._options['tmbURL'] = ''
            self._options['tmbPath'] = ''
            #read only volume
            self._attributes.insert(0, {
                'pattern' : r'.*',
                'read' : False
            })

        self._options['URL'] = self._urlize(self._options['URL'])
        self._options['tmbURL'] = self._urlize(self._options['tmbURL'])
        
        self._checkArchivers()
        self._configure()
        
        self._mounted = True
        
    def _configure(self):
        """
        Configure after successful mount.
        By default set thumbnail path.
        """
        #set thumbnails path
        if self._options['tmbPath']:
            path = self._join_path(self._root, self._options['tmbPath'])
            
            self._attributes.append({
                'pattern' : '^%s$' % re.escape('%s%s' % (self._separator, self._relpath(path))),
                'locked' : True,
                'hidden' : True
            })
            
            try:
                stat = self.stat(path)
            except os.error:
                try:
                    self._mkdir(path=path, mode=self._options['tmbPathMode'])
                    stat = self.stat(path)
                except os.error:
                    stat = None

            if stat and stat['mime'] == 'directory' and stat['read']:
                self._options['tmbPath'] = path
                self._tmb_path_writable = stat['write']          
            else:
                self._options['tmbPath'] = ''
    
    def unmount(self):
        """
        Some "unmount" stuff - may be required by virtual fs
        """
        #TODO: maybe delete cache here
        pass
    
    def default_path(self):
        """
        Return root or startPath hash
        """
        return self.encode(self._start_path if self._start_path else self._root)
    
    def upload_max_size(self):
        """
        Return the upload max size.
        """
        return self._options['uploadMaxSize']
    
    def options(self, hash_):
        """
        Return volume options required by client
        """
        return {
            'path' : self._path(self.decode(hash_)),
            'url' : self._options['URL'],
            'tmbUrl' : self._options['tmbURL'],
            'disabled' : self._options['disabled'],
            'separator' : self._separator,
            'copyOverwrite' : self._options['copyOverwrite'],
            'archivers' : {
                'create' : self._archivers['create'].keys(),
                'extract' : self._archivers['extract'].keys()
            }
        }
    
    def command_disabled(self, cmd):
        """
        Return True if command disabled in options
        """
        return cmd in self._options['disabled']
    
    def set_mimes_filter(self, mimes):
        """
        Set mimetypes allowed to display in client
        """
        self._options['onlyMimes'] = mimes

    def mime_accepted(self, mime, mimes = [], empty = True):
        """
        Return True if mime is in required mimes list
        """
        mimes = mimes if mimes else self._options['onlyMimes']
        if not mimes:
            return empty

        return mime == 'directory' or 'all' in mimes or 'All' in mimes or mime in mimes or mime[0:mime.find('/')] in mimes
    
    def is_readable(self):
        """
        Return True if root is readable.
        """
        return self.stat(self._root)['read']
    
    def copy_from_allowed(self):
        """
        Return True if copy from this volume allowed
        """
        return self._options['copyFrom']
    
    def path(self, hash_):
        """
        Return file path related to root
        """
        return self._path(self.decode(hash_))
    
    def removed(self):
        """
        Return list of moved/overwrited files
        """
        return self._removed
    
    def reset_removed(self):
        """
        Clean removed files list
        """
        self._removed = []
    
    def closest(self, hash_, attr, val):
        """
        Return file/dir hash or first founded child hash with 
        required attr == val
        """
        path = self.closestByAttr(self.decode(hash_), attr, val)
        return self.encode(path) if path else False
    
    def file(self, hash_):
        """
        Return file info or False on error. Raises a FileNotFoundError if file was not found
        """
        try:
            return self.stat(self.decode(hash_))
        except os.error:
            raise FileNotFoundError

    def dir(self, hash_, resolveLink=False):
        """
        Return folder info. Raises a DirNotFoundError if hash_ path is not a directory, 
        or FileNotFoundError if the hash_  path is not a valid file at all
        """

        dir_ = self.file(hash_)
        if resolveLink and 'thash' in dir_ and dir_['thash']:
            dir_ = self.file(dir_['thash'])
            
        if dir_['mime'] != 'directory' or self._is_hidden(dir_):
            raise DirNotFoundError
        
        return dir_
    
    def scandir(self, hash_):
        """
        Return directory content or False on error. 
        Raises a DirNotFoundError if hash_ is not a valid dir, 
        or a PermissionDenied Error if the user cannot access the data
        """
        if not self.dir(hash_)['read']:
            raise PermissionDeniedError            
        return self.getScandir(self.decode(hash_))

    def ls(self, hash_):
        """
        Return dir files names list. Raises
        PermissionDeniedError, FileNotFoundError, DirNotFoundError
        """
        if not self.dir(hash_)['read']:
            raise PermissionDeniedError
        
        list_ = []
        path = self.decode(hash_)
       
        for stat in self.getScandir(path):
            if not self.isHiddden(stat) and self.mime_accepted(stat['mime']):
                list_.append(stat['name'])

        return list_

    def tree(self, hash_='', deep=0, exclude=''):
        """
        Return subfolders for required folder or False on error
        """
        path = self.decode(hash_) if hash_ else self._root

        dirs = [self.stat(path)]
        if dirs[0]['mime'] != 'directory':
            return []
        
        try:
            excluded = self.decode(exclude)
        except FileNotFoundError:
            excluded = None
                
        return dirs + self.gettree(path, (deep - 1) if deep > 0 else (self._options['treeDeep'] - 1), excluded)
    
    def parents(self, hash_):
        """
        Return part of dirs tree from required dir up to root dir
        Raises DirNotFoundError, FileNotFoundError, PermissionDenied
        """
        current = self.dir(hash_)
        path = self.decode(hash_)
        tree = []
        
        while path and path != self._root:
            path = self._dirname(path)
            stat = self.stat(path)
            if self._is_hidden(stat) or not stat['read']:
                raise PermissionDeniedError
            
            tree[:0] = [stat]
            if path != self._root:
                for dir_ in self.gettree(path, 0):
                    if not dir_ in tree:
                        tree.append(dir_)

        return tree if tree else [current]
    
    def tmb(self, hash_):
        """
        Create thumbnail for required file and return its name or None on fail.
        Will raise exception upon fail.
        """
    
        path = self.decode(hash_)
        stat = self.file(hash_)
        name = self._tmb_name(stat)
        
        if 'tmb' in stat and stat['tmb'] != 1:
            return stat['tmb']
        
        if not self._can_create_tmb(path, stat):
            raise PermissionDeniedError()

        #copy the image to the thumbnail
        tmb  = self._join_path(self._options['tmbPath'], name)
        tmb_size = self._options['tmbSize']
        
        try:
            im = self._openimage(path)
            s = im.size
        except:
            raise NotAnImageError
    
        # If image smaller or equal thumbnail size - just fitting to thumbnail square 
        if s[0] <= tmb_size and s[1]  <= tmb_size:
            self._img_square_fit(path, tmb, tmb_size, tmb_size, self._options['tmbBgColor'], 'png')
        elif self._options['tmbCrop']:
            #Resize and crop if image bigger than thumbnail
            if s[0] > tmb_size and s[1] > tmb_size:
                s = self._img_resize(path, tmb, tmb_size, tmb_size, True, False, 'png')
                try:
                    self._img_crop(tmb, tmb, tmb_size, tmb_size, int((s[0] - tmb_size)/2), int((s[1] - tmb_size)/2), 'png')
                except:
                    self._unlink(tmb)
                    raise
            else:
                self._img_crop(path, tmb, tmb_size, tmb_size, 
                    int((s[0] - tmb_size)/2) if s[0] > tmb_size else 0,
                    int((s[1] - tmb_size)/2) if s[1] > tmb_size else 0, 'png')
        else:
            try:
                im.thumbnail((tmb_size, tmb_size), Image.ANTIALIAS)
                self._saveimage(im, tmb, 'png')
                self._img_square_fit(tmb, tmb, tmb_size, tmb_size, self._options['tmbBgColor'], 'png' )
            except:
                self._unlink(tmb)
                raise

        #TODO: shouldn't we clear the cache for this file?
        return name
    
    def size(self, hash_):
        """
        Return file size / total directory size
        """
        return self._size(self.decode(hash_))
    
    def open(self, hash_):
        """
        Open file for reading and return file pointer
        """
 
        if self.file(hash_)['mime'] == 'directory':
            raise FileNotFoundError
        
        return self._fopen(self.decode(hash_), 'rb')
    
    def close(self, fp, hash_):
        """
        Close file pointer
        """
        self._fclose(fp, path=self.decode(hash_))

    def mkdir(self, hash_dst, name):
        """
        Create directory and return dir info
        """

        if self.command_disabled('mkdir'):
            raise PermissionDeniedError
        
        if not self._name_accepted(name):
            raise Exception(ElfinderErrorMessages.ERROR_INVALID_NAME)
        
        error_path = self.path(hash_dst)

        try:
            dir_ = self.dir(hash_dst) 
        except (FileNotFoundError, DirNotFoundError):
            raise NamedError(ElfinderErrorMessages.ERROR_TRGDIR_NOT_FOUND, '#%s' % error_path)
        
        if not dir_['write']:
            raise PermissionDeniedError
        
        path = self.decode(hash_dst)
        dst  = self._join_path(path, name)
        
        try:
            self.stat(dst)
            raise NamedError(ElfinderErrorMessages.ERROR_EXISTS, name)
        except:
            #TODO: clear only container cache?
            self.clearcache()

        return self.stat(self._mkdir(dst))
    
    def mkfile(self, hash_dst, name):
        """
        Create empty file and return its info
        """

        if self.command_disabled('mkfile'):
            raise PermissionDeniedError
        
        if not self._name_accepted(name):
            raise Exception(ElfinderErrorMessages.ERROR_INVALID_NAME)
        
        error_path = self.path(hash_dst)

        try:
            dir_ = self.dir(hash_dst)
        except (FileNotFoundError, DirNotFoundError):
            raise NamedError(ElfinderErrorMessages.ERROR_TRGDIR_NOT_FOUND, '#%s' % error_path)
        
        path = self.decode(hash_dst)
        if not dir_['write'] or not self.attr(self._join_path(path, name), 'write'):
            raise PermissionDeniedError
        
        try:
            self.stat(self._join_path(path, name))
            raise NamedError(ElfinderErrorMessages.ERROR_EXISTS, name)
        except os.error:
            pass
    
        #TODO: clear only containing dir cache?
        self.clearcache()
        return self.stat(self._mkfile(path, name))

    def rename(self, hash_, name):
        """
        Rename file and return file info
        """

        path = self.decode(hash_)
        dir_  = self._dirname(path)

        if self.command_disabled('rename') or not self.attr(self._join_path(dir_, name), 'write'):
            raise PermissionDeniedError
        
        if not self._name_accepted(name):
            raise Exception(ElfinderErrorMessages.ERROR_INVALID_NAME)

        file_ = self.file(hash_)
        file_['realpath'] = path

        if name == self._basename(path):
            return (file_, file_)
        
        if self._is_locked(file_):
            raise NamedError(ElfinderErrorMessages.ERROR_LOCKED, file_['name'])
          
        try:
            self.stat(self._join_path(dir_, name))
            raise NamedError(ElfinderErrorMessages.ERROR_EXISTS, name)
        except os.error:
            pass

        self.rmTmb(file_) #remove old name tmbs, we cannot do this after dir move
        path = self._move(path, dir_, name)
        
        if not path:
            raise Exception('Unable to rename the file')
        
        #TODO: clear specific keys only?
        self.clearcache()
        self._removed.append(file_)

        return self.stat(path) 

    def duplicate(self, hash_, suffix='copy'):
        """
        Create file copy with suffix "copy number" and return its info
        """

        if self.command_disabled('duplicate'):
            raise PermissionDeniedError
        
        #check if source file exists, throw FileNotFoundError if it doesn't
        self.file(hash_)

        path = self.decode(hash_)
        dir_  = self._dirname(path)
        name = self._unique_name(dir_, self._basename(path), ' %s ' % suffix)

        if not self.attr(self._join_path(dir_, name), 'write'):
            raise PermissionDeniedError

        return self.stat(self.copy(path, dir_, name))
    
    def upload(self, uploaded_file, hash_dst):
        """
        Save uploaded file. 
        On success return array with new file stat and with removed file hash (if existed file was replaced)
        """

        if self.command_disabled('upload'):
            raise PermissionDeniedError
        
        error_path = self.path(hash_dst)
    
        try:
            dir_ = self.dir(hash_dst)
        except (FileNotFoundError, DirNotFoundError):
            raise NamedError(ElfinderErrorMessages.ERROR_TRGDIR_NOT_FOUND, '#%s' % error_path)

        if not dir_['write']:
            raise PermissionDeniedError
        
        if not self._name_accepted(uploaded_file.name):
            raise Exception(ElfinderErrorMessages.ERROR_INVALID_NAME)
        
        mime = uploaded_file.content_type 

        #logic based on http://httpd.apache.org/docs/2.2/mod/mod_authz_host.html#order
        allow = self.mime_accepted(mime, self._options['uploadAllow'], None)
        deny   = self.mime_accepted(mime, self._options['uploadDeny'], None)
        if self._options['uploadOrder'][0].lower() == 'allow': #['allow', 'deny'], default is to 'deny'
            upload = False #default is deny
            if not deny and allow == True: #match only allow
                upload = True
            #else: (both match | no match | match only deny): deny
        else: #['deny', 'allow'], default is to 'allow' - this is the default rule
            upload = True #default is allow
            if deny == True and not allow: #match only deny
                upload = False
            #else: (both match | no match | match only allow): allow }
        
        if not upload:
            raise Exception(ElfinderErrorMessages.ERROR_UPLOAD_FILE_MIME)

        if self._options['uploadMaxSize'] > 0 and uploaded_file.size > self._options['uploadMaxSize']:
            raise Exception(ElfinderErrorMessages.ERROR_UPLOAD_FILE_SIZE)

        dst = self.decode(hash_dst)
        name = uploaded_file.name
        test = self._join_path(dst, name)

        try:
            file_ = self.stat(test)
            #file exists
            if self._options['uploadOverwrite']:
                if not file_['write']:
                    raise PermissionDeniedError
                elif file_['mime'] == 'directory':
                    raise NamedError(ElfinderErrorMessages.ERROR_NOT_REPLACE, uploaded_file.name)
                self.remove(test)
            else:
                name = self._unique_name(dst, uploaded_file.name, '-', False)
        except os.error: #file does not exist
            pass
        
        kwargs = {}
        try:
            im = Image.open(uploaded_file.temporary_file_path)
            (kwargs['w'], kwargs['h']) = im.size
        except:
            pass #file is not an image

        #TODO: clear containing dir cache?
        self.clearcache()
        return self.stat(self._save_uploaded(uploaded_file, dst, name, **kwargs))
    
    def paste(self, volume, hash_src, dst, rm_src = False):
        """
        Paste files
        """
        
        if self.command_disabled('paste') or (volume==self and rm_src and self.command_disabled('rm')) (volume!=self and rm_src and volume.command_disabled('rm')):
            raise PermissionDeniedError

        #check if src file exists, throw FileNotFoundError if it doesn't
        file_ = volume.file(hash_src)
        name = file_['name']
        error_path = volume.path(hash_src)
        
        try:
            dir_ = self.dir(dst)
        except (FileNotFoundError, DirNotFoundError):
            raise NamedError(ElfinderErrorMessages.ERROR_TRGDIR_NOT_FOUND, '#%s' % dst)
        
        if not dir_['write'] or not file_['read']:
            raise PermissionDeniedError

        destination = self.decode(dst)

        test = volume.closest(hash_src, 'locked' if rm_src else 'read', rm_src)
        if test:
            raise NamedError(ElfinderErrorMessages.ERROR_LOCKED, volume.path(test)) if rm_src else PermissionDeniedError

        test = self._join_path(destination, name)
        try:
            stat = self.stat(test)
            #file exists
            if self._options['copyOverwrite']:
                #do not replace file with dir or dir with file
                if (file_['mime'] == 'directory' and stat['mime'] != 'directory') or (file_['mime'] != 'directory' and stat['mime'] == 'directory'):
                    raise NamedError(ElfinderErrorMessages.ERROR_NOT_REPLACE, self._path(test))
                #existed file is not writable
                if not stat['write']:
                    raise PermissionDeniedError()
                #existed file locked or has locked child
                locked = self.closestByAttr(test, 'locked', True)
                if locked:
                    raise NamedError(ElfinderErrorMessages.ERROR_LOCKED, self._path(locked))
                #target is entity file of alias
                if volume == self and (test == file_['target'] or test == self.decode(hash_src)):
                    raise NamedError(ElfinderErrorMessages.ERROR_REPLACE, error_path)
                #remove existing file
                self.remove(test)
            else:
                name = self._unique_name(destination, name, ' ', False)
        except os.error:
            pass
        
        #copy/move inside current volume
        if (volume == self):
            source = self.decode(hash_src)
            #do not copy into itself
            if self._inpath(destination, source):
                raise NamedError(ElfinderErrorMessages.ERROR_COPY_ITSELF, error_path)
            if rm_src:
                path = self.move(source, destination, name)
            else:
                path = self.copy(source, destination, name)
        else:
            #copy/move from another volume
            if not self._options['copyTo'] or not volume.copy_from_allowed():
                raise PermissionDeniedError()
        
            path = self.copyFrom(volume, hash_src, destination, name)
        
            if rm_src:
                try:
                    volume.rm(hash_src)
                except:
                    raise Exception(ElfinderErrorMessages.ERROR_RM_SRC)

        return self.stat(path)

    def get_contents(self, hash_):
        """
        Return file contents
        """

        file_ = self.file(hash_)
        
        if file_['mime'] == 'directory':
            raise Exception(ElfinderErrorMessages.ERROR_NOT_FILE)
        
        if not file_['read']:
            raise PermissionDeniedError()
        
        return self._get_contents(self.decode(hash_))
    
    def put_contents(self, hash_, content):
        """
        Put content in text file and return file info.
        """
        if self.command_disabled('edit'):
            raise PermissionDeniedError()
        
        path = self.decode(hash_)
        file_ = self.file(hash_)
        
        if not file_['write']:
            raise PermissionDeniedError()

        #TODO: clear specific cache key
        self.clearcache()
        self._put_contents(path, content)
        return self.stat(path)
    
    def extract(self, hash_):
        """
        Extract files from archive
        """

        if self.command_disabled('extract'):
            raise PermissionDeniedError()
        
        file_ = self.file(hash_)

        archiver = self._archivers['extract'][file_['mime']] if file_['mime'] in self._archivers['extract'] else False
        if not archiver:
            raise Exception(ElfinderErrorMessages.ERROR_NOT_ARCHIVE)
        
        path = self.decode(hash_)
        parent = self.stat(self._dirname(path))

        if not file_['read'] or not parent['write']:
            raise PermissionDeniedError()

        self.clearcache()
        path = self._extract(path, archiver)
        return self.stat(path)

    def archive(self, hashes, mime):
        """
        Add files to archive
        """

        if self.command_disabled('archive'):
            raise PermissionDeniedError()

        archiver = self._archivers['create'][mime] if mime in self._archivers['create'] else False
        if not archiver:
            raise Exception(ElfinderErrorMessages.ERROR_ARCHIVE_TYPE)
        
        files = []
        for hash_ in hashes:
            file_ = self.file(hash_)
            
            if not file_['read']:
                raise PermissionDeniedError()

            path = self.decode(hash_)
            try: #check if dir_ is set
                dir_ #@UndefinedVariable
            except:
                dir_ = self._dirname(path)
                stat = self.stat(dir_)
                if not stat['write']:
                    raise PermissionDeniedError()

            files.append(self._basename(path))
        
        name = '%s.%s' % (files[0] if len(files) == 1 else 'Archive', archiver['ext'])
        name = self._unique_name(dir_, name, '')
        
        self.clearcache()
        path = self._archive(dir_, files, name, archiver)
        return self.stat(path)

    def resize(self, hash_, width, height, x, y, mode = 'resize', bg = '', degree = 0):
        """
        Resize image.
        """

        if self.command_disabled('resize'):
            raise PermissionDeniedError()
        
        file_ = self.file(hash_)
        
        if not file_['write'] or not file_['read']:
            raise PermissionDeniedError()
        
        if not file_['mime'].startswith('image'):
            raise Exception(ElfinderErrorMessages.ERROR_UNSUPPORT_TYPE)

        path = self.decode(hash_)
        if mode == 'propresize':
            result = self._img_resize(path, path, width, height, True, True)
        elif mode == 'crop':
            result = self._img_crop(path, path, width, height, x, y)
        elif mode == 'fitsquare':
            result = self._img_square_fit(path, path, width, height, bg if bg else self._options['tmbBgColor'])
        elif mode == 'rotate':
            result = self._img_rotate(path, path, degree, bg if bg else self._options['tmbBgColor'])
        else:
            result = self._img_resize(path, path, width, height, False, True)

        if result:
            self.rmTmb(file_)
            self.clearcache()
            return self.stat(path)
        
        raise Exception(_('Could not resize image'))

    def rm(self, hash_):
        """
        Remove file/dir
        """
        if self.command_disabled('rm'):
            raise PermissionDeniedError()
        return self.remove(self.decode(hash_))
    
    def search(self, q):
        """
        Search files
        """
        return self._search(self._root, q)

    def dimensions(self, hash_):
        """
        Return image dimensions.
        Raise FileNotFoundError or NotAnImageError
        """
        return self._dimensions(self.decode(hash_), self.file(hash_)['mime'])
        
    #*********************************************************************#
    #*                               FS API                              *#
    #*********************************************************************#
    
    #***************** paths *******************#
    
    def encode(self, path):
        """
        Encode path into hash
        """
        if path:
            #cut ROOT from path for security reason, even if hacker decodes the path he will not know the root
            #files hashes will also be valid, even if root changes
            p = self._relpath(path)
            #if reqesting root dir path will be empty, then assign '/' as we cannot leave it blank for crypt
            if not p:
                p = self._separator

            hash_ = self._crypt(p)
            #hash is used as id in HTML that means it must contain vaild chars
            #make base64 html safe and append prefix in begining
            hash_ = b64encode(hash_).translate(maketrans('+/=', '-_.'))

            #remove dots '.' at the end (used to be '=' in base64, before the translation)
            hash_ = hash_.rstrip('.')

            #append volume id to make hash unique
            return self.id()+hash_
    
    def decode(self, hash_):
        """
        Decode path from hash
        """
        if hash_.startswith(self.id()):
            #cut volume id after it was prepended in encode
            h = hash_[len(self.id()):]
            #replace HTML safe base64 to normal
            h = h.encode('ascii').translate(maketrans('-_.', '+/='))
            #put cut = at the end
            h += "=" * ((4 - len(h) % 4) % 4)
            h = b64decode(h)

            path = self._uncrypt(h) 
            #append ROOT to path after it was cut in encode
            return self._abspath(path)

        raise FileNotFoundError
    
    def _crypt(self, path):
        """
        Return crypted path.
        """
        #TODO: crypt and encrypt paths
        return path
    
    def _uncrypt(self, hash_):
        """
        Return uncrypted path.
        """
        return hash_
    
    #*********************** file stat *********************#
    
    def attr(self, path, attr, val=False):
        """
        Check file attribute
        """
        
        if not attr in self._defaults:
            return False

        #TODO: replace this with signals??
        if self._options['accessControl'] and hasattr(self._options['accessControl'], '__call__'):
            perm = self._options['accessControl'](attr, path, self)
            if perm != None:
                return perm

        for attrs in self._attributes:
            if attr in attrs and re.search(attrs['pattern'], '%s%s' % (self._separator, self._relpath(path))):
                return attrs[attr]
                
        return self._defaults[attr] if not val else val

    def stat(self, path):
        """
        Return fileinfo. Raises os.error if the path is invalid
        """
        statCache = cache.get(self._get_cache_keys()[1], {})
        if not path in statCache:
            self.updateCache(path, self._stat(path), statCache)
            cache.set(self._get_cache_keys()[1], statCache)
        return statCache[path]

    def updateCache(self, path, stat, statCache):
        """
        Put file stat in cache and return it
        """
        if not stat:
            statCache[path] = {}
            return

        stat['hash'] = self.encode(path)

        if path == self._root:
            stat['volumeid'] = self.id()
            stat['name'] = self._root_name
        else:
            if not 'name' in stat or not stat['name']:
                stat['name'] = self._basename(path)

            if not 'phash' in stat or not stat['phash']:
                stat['phash'] = self.encode(self._dirname(path))

        if not 'mime' in stat or not stat['mime']:
            stat['mime'] = self.mimetype(stat['name'])
            
        if not 'size' in stat or (not stat['size'] and stat['mime'] == 'directory'):
            stat['size'] = 'unknown'

        stat['read'] = int(self.attr(path, 'read', stat['read']))
        stat['write'] = int(self.attr(path, 'write', stat['write']))
        stat['locked'] = int(self.attr(path, 'locked', self._is_locked(stat)))
        stat['hidden'] = int(self.attr(path, 'hidden', self._is_hidden(stat)) and self.mime_accepted(stat['mime'])) 

        if stat['read'] and not self._is_hidden(stat):

            if stat['mime'] == 'directory': #handle directories
                if self._options['checkSubfolders']:
                    if 'dirs' in stat:
                        if not stat['dirs']:
                            del stat['dirs']
                    elif 'alias' in stat and stat['alias'] and 'target' in stat and stat['target']:
                        stat['dirs'] = int('dirs' in statCache[stat['target']]) if stat['target'] in statCache else int(self._subdirs(stat['target'])) 
                    elif self._subdirs(path):
                        stat['dirs'] = 1
                else:
                    stat['dirs'] = 1
            else: #file
                if not 'tmb' in stat and self._can_create_tmb(path, stat):
                    stat['tmb'] = self._get_tmb(stat['target'] if 'target' in stat else path, stat)
        
        if 'alias' in stat and stat['alias'] and 'target' in stat and stat['target']:
            stat['thash'] = self.encode(stat['target'])
            del stat['target']

        statCache[path] = stat

    def _cache_dir(self, path):
        """
        Get stat for folder content and put in the cache.
        """
        dirsCache = cache.get(self._get_cache_keys()[0], {})
        dirsCache[path] = []

        for p in self._scandir(path):
            stat = self.stat(p)
            if not self._is_hidden(stat):
                dirsCache[path].append(p)
        cache.set(self._get_cache_keys()[0], dirsCache)
        return dirsCache[path]

    def _get_cached_dir(self, path):
        """
        Returns the cached dictionary for this path
        """
        cached_dirs = cache.get(self._get_cache_keys()[0], {})
        return cached_dirs[path] if path in cached_dirs else []

    def clearcache(self):
        """
        Clean cached directory stat info
        """
        for cachekey in self._get_cache_keys():
            cache.delete(cachekey)

    def mimetype(self, path, name = ''):
        """
        Return file mimetype.  
        """
        mime = self._mimetype(path)
        int_mime = None

        if not mime or mime in ['inode/x-empty', 'application/empty']:
            int_mime = self.mimetypeInternalDetect(name if name else path)
                
        return int_mime if int_mime else mime

    def mimetypeInternalDetect(self, path):
        """
        Detect file mimetype using "internal" method
        """
        return mimetypes.guess_type(path)[0]
    
    def _size(self, path):
        """
        Return file/total directory size
        """
        try:
            stat = self.stat(path)
        except os.error:
            return 0  
        
        if not stat['read'] or self._is_hidden(stat):
            return 0
        
        if stat['mime'] != 'directory':
            return stat['size']
        
        subdirs = self._options['checkSubfolders']
        self._options['checkSubfolders'] = True
        result = 0

        for stat in self.getScandir(path):
            size = self._size(self._join_path(path, stat['name'])) if stat['mime'] == 'directory' and stat['read'] else stat['size']
            if (size > 0):
                result += size

        self._options['checkSubfolders'] = subdirs
        return result

    def closestByAttr(self, path, attr, val):
        """
        If file has required attr == val - return file path,
        If dir has child with has required attr == val - return child path
        """
        try:
            stat = self.stat(path)
        except os.error:
            return False
        
        v = stat[attr] if attr in stat else False
        if v == val:
            return path

        return self.childsByAttr(path, attr, val) if stat['mime'] == 'directory' else False

    def childsByAttr(self, path, attr, val):
        """
        Return first found children with required attr == val
        """
        for p in self._scandir(path):
            _p = self.closestByAttr(p, attr, val)
            if _p != False:
                return _p
        return False

    #*****************  get content *******************#

    def getScandir(self, path):
        """
        Return required directory files info.
        If onlyMimes is set - return only dirs and files of required mimes
        """
        files = []
        dir_ = self._get_cached_dir(path)
        if not dir_:
            dir_ = self._cache_dir(path)

        for p in dir_:
            stat = self.stat(p)
            if not self._is_hidden(stat):
                files.append(stat)

        return files

    def gettree(self, path, deep, exclude=''):
        """
        Return subdirs tree
        """
        dirs = []
        
        dirsCache = self._get_cached_dir(path)
        if not dirsCache:
            dirsCache = self._cache_dir(path)

        for p in dirsCache:
            stat = self.stat(p)
            if not self._is_hidden(stat) and p != exclude and stat['mime'] == 'directory':
                dirs.append(stat)
                if deep > 0 and 'dirs' in stat and stat['dirs']:
                    dirs += self.gettree(p, deep-1)
        return dirs

    def _search(self, path, q):
        """
        Recursively search for files in the specified path,
        based on the ``q`` query.
        """
        result = []
        for p in self._scandir(path):
            try:
                stat = self.stat(p)
            except os.error: #invalid links
                continue

            if self._is_hidden(stat) or not self.mime_accepted(stat['mime']):
                continue
            
            name = stat['name']

            if q in name:
                stat['path'] = self._path(p)
                if self._options['URL'] and not 'url' in stat:
                    stat['url'] = self._options['URL'] + p[len(self._root) + 1:].replace(self._separator, '/')
                result.append(stat)

            if stat['mime'] == 'directory' and stat['read'] and not 'alias' in  stat:
                result += self._search(p, q)

        return result
        
    #**********************  manipulations  ******************#

    def copy(self, src, dst, name):
        """
        Copy file/recursive copy dir only in current volume.
        Return new file path or False.
        """

        srcStat = self.stat(src)
        self.clearcache()
        
        if 'thash' in srcStat and srcStat['thash']:           
            target = self.decode(srcStat['thash'])
            stat = self.stat(target)
            self.clearcache()
            
            try:
                self._symlink(target, dst, name)
            except:
                raise NamedError(ElfinderErrorMessages.ERROR_COPY, self._path(src))
            
            return self._join_path(dst, name)
        
        if srcStat['mime'] == 'directory':
            test = self.stat(self._join_path(dst, name))
            
            if test['mime'] != 'directory': 
                raise NamedError(ElfinderErrorMessages.ERROR_COPY, self._path(src))
            
            try:
                self._mkdir(dst, name)
            except:
                raise NamedError(ElfinderErrorMessages.ERROR_COPY, self._path(src)) 
            
            dst = self._join_path(dst, name)
            for stat in self.getScandir(src):
                if not self._is_hidden(stat):
                    name = stat['name']
                    try:
                        self.copy(self._join_path(src, name), dst, name)
                    except:
                        self.remove(dst, True) #fall back
                        return None

            self.clearcache()
            return dst 

        try:
            self._copy(src, dst, name)
        except:
            raise NamedError(ElfinderErrorMessages.ERROR_COPY, self._path(src))
        
        return self._join_path(dst, name)

    def move(self, src, dst, name):
        """
        Move file
        Return new file path or False.
        """

        stat = self.stat(src)
        stat['realpath'] = src
        self.rmTmb(stat) #can not do rmTmb() after _move()
        self.clearcache()
        
        try:
            self._move(src, dst, name)
            self._removed.append(stat)
            return self._join_path(dst, name)
        except:
            raise NamedError(ElfinderErrorMessages.ERROR_MOVE, self._path(src))

    def copyFrom(self, volume, src, destination, name):
        """
        Copy file from another volume.
        Return new file path or False.
        """ 

        try:
            source = volume.file(src)
        except FileNotFoundError:
            raise NamedError(ElfinderErrorMessages.ERROR_COPY, '#'.src, volume.error())
        
        errpath = volume.path(src)
        
        if not self._name_accepted(source['name']):
            raise Exception(ElfinderErrorMessages.ERROR_INVALID_NAME)
                
        if not source['read']:
            raise PermissionDeniedError()
        
        if source['mime'] == 'directory':
            stat = self.stat(self._join_path(destination, name))
            self.clearcache()
            if stat['mime'] != 'directory' and not self._mkdir(destination, name):
                raise NamedError(ElfinderErrorMessages.ERROR_COPY, errpath)
            
            path = self._join_path(destination, name) 
            for entry in volume.scandir(src):
                if not self.copyFrom(volume, entry['hash'], path, entry['name']):
                    return False
        else:
            mime = source['mime']
            kwargs = { 'source' : source }

            if mime.startswith('image'):
                try:
                    dim = volume.dimensions(src)
                    s = dim.split('x')
                    kwargs['w'] = s[0]
                    kwargs['h'] = s[1]
                except NotAnImageError:
                    pass

            try:
                fp = volume.open(src)
                path = self._save(fp, destination, name, **kwargs)
                volume.close(fp, src)
            except:
                raise NamedError(ElfinderErrorMessages.ERROR_COPY, errpath)
        
        return path

    def remove(self, path, force = False):
        """
        Remove file/ recursive remove dir
        """
        try:
            stat = self.stat(path)
        except os.error:
            raise NamedError(ElfinderErrorMessages.ERROR_RM, self._path(path))

        stat['realpath'] = path
        self.rmTmb(stat)
        self.clearcache()
        
        if not force and self._is_locked(stat):
            raise NamedError(ElfinderErrorMessages.ERROR_LOCKED, self._path(path))

        if stat['mime'] == 'directory':
            if self.command_disabled('rmdir'):
                raise PermissionDeniedError()
            
            for p in self._scandir(path):
                self.remove(p)

            try:
                self._rmdir(path)
            except:
                raise NamedError(ElfinderErrorMessages.ERROR_RM, self._path(path))

        else:
            try:
                self._unlink(path)
            except:
                raise NamedError(ElfinderErrorMessages.ERROR_RM, self._path(path))

        #TODO: update cache key
        self._removed.append(stat)
    
    #************************* thumbnails **************************#

    def _tmb_name(self, stat):
        """
        Return thumbnail file name for required file
        """
        return '%s%s.png' % (stat['hash'], stat['ts'])

    def _get_tmb(self, path, stat):
        """
        Return thumnbnail name if exists
        """
        if self._options['tmbURL'] and self._options['tmbPath']:
            #file itself thumnbnail
            if path.startswith(self._options['tmbPath']):
                return self._basename(path)

            name = self._tmb_name(stat)
            try:
                self.stat(self._join_path(self._options['tmbPath'], name))
                return name
            except os.error:
                pass
        #default thumbnail value
        return 1

    def _can_create_tmb(self, path, stat):
        """
        Return True if thumnbnail for required file can be created.
        """
        return self._tmb_path_writable and not path.startswith(self._options['tmbPath']) and stat['mime'].startswith('image') 

    def _img_resize(self, path, target, width, height, keepProportions = False, resizeByBiggerSide = True, destformat = None):
        """
        Resize image and return the new size.
        """
        try:
            im = self._openimage(path)
            s = im.size
        except:
            raise NotAnImageError
    
        size_w = width
        size_h = height

        if keepProportions:
            orig_w, orig_h = s[0], s[1]
            
            #Resizing by biggest side
            if resizeByBiggerSide:
                if (orig_w > orig_h):
                    size_h = orig_h * width / orig_w
                    size_w = width
                else:
                    size_w = orig_w * height / orig_h
                    size_h = height
            elif orig_w > orig_h:
                size_w = orig_w * height / orig_h
                size_h = height
            else:
                size_h = orig_h * width / orig_w
                size_w = width

        resized = im.resize((size_w, size_h), Image.ANTIALIAS)
        self._saveimage(resized, target, destformat if destformat else im.format)
        
        return resized.size

    def _img_crop(self, path, target, width, height, x, y, destformat = None):
        """
        Crop image.
        """
        try:
            im = self._openimage(path)
        except:
            raise NotAnImageError

        cropped = im.crop((x, y, width+x, height+y))
        self._saveimage(cropped, target, destformat if destformat else im.format)

        return path

    def _img_square_fit(self, path, target, width, height, bgcolor = '#0000ff', destformat = None):
        """
        Put image to square
        """
        try:
            im = self._openimage(path)
        except:
            raise NotAnImageError

        bg = Image.new('RGB', (width, height), bgcolor)

        if im.mode == 'RGBA':
            bg.paste(im, ((width-im.size[0])/2, (height-im.size[1])/2), im)
        else: #do not use a mask if file is not in RGBA mode.
            bg.paste(im, ((width-im.size[0])/2, (height-im.size[1])/2))

        self._saveimage(bg, target, destformat if destformat else im.format)

        return path

    def img_rotate(self, path, target, degree, bgcolor = '#ffffff', destformat = None):
        """
        Rotate image
        """
        try:
            tempim = self._openimage(path)
            im = tempim.convert('RGBA')
        except:
            raise NotAnImageError

        rotated = im.rotate(angle=degree)
        bg = Image.new('RGB', rotated.size, bgcolor)
        
        result = Image.composite(rotated, bg, rotated)
        self._saveimage(result, target, destformat if destformat else im.format)
        
        return path

    def rmTmb(self, stat):
        """
        Remove thumbnail, also remove recursively if stat is directory
        """
        if stat['mime'] == 'directory':
            for p in self._scandir(self.decode(stat['hash'])):
                self.rmTmb(self.stat(p))
        elif 'tmb' in stat and stat['tmb'] != 1:
            tmb = self._join_path(self._options['tmbPath'], stat['tmb'])
            try:
                self._unlink(tmb)
            except:
                pass
            self.clearcache()

    #******************* archive files **********************#
    
    def _checkArchivers(self):
        """
        Detect available archivers
        """
        self._archivers = {
            'create'  : { 'application/x-tar' : { 'ext' : 'tar' , 'archiver' : TarFile }, 
                         'application/x-gzip' : { 'ext' : 'tgz' , 'archiver' : TarFile },
                         'application/x-bzip2' : { 'ext' : 'tbz' , 'archiver' : TarFile },
                         'application/zip' : { 'ext' : 'zip' , 'archiver' : ZipFileArchiver }
                         },
            'extract' : { 'application/x-tar' : { 'ext' : 'tar' , 'archiver' : TarFile }, 
                         'application/x-gzip' : { 'ext' : 'tgz' , 'archiver' : TarFile },
                         'application/x-bzip2' : { 'ext' : 'tbz' , 'archiver' : TarFile },
                         'application/zip' : { 'ext' : 'zip' , 'archiver' : ZipFileArchiver }
                         }
        }
        
        #control available archive types from the options
        if 'archiveMimes' in self._options and self._options['archiveMimes']:
            for mime in self._archivers['create']:
                if not mime in self._options['archiveMimes']:
                    del self._archivers['create'][mime]
        
        #manualy add archivers
        if 'create' in self._options['archivers']:
            for mime, archiver in self._options['archivers']['create'].items():
                try:
                    conf = archiver['archiver']
                    archiver['ext']
                except:
                    continue
                #check if conf is class and implements open, add and close methods
                if re.match(r'application/', mime) and inspect.isclass(conf) and hasattr(conf, 'open') and callable(getattr(conf, 'open')) and hasattr(conf, 'add') and callable(getattr(conf, 'add')) and hasattr(conf, 'close') and callable(getattr(conf, 'close')):
                    self._archivers['create'][mime] = archiver

        if 'extract' in self._options['archivers']:
            for mime, archiver in self._options['archivers']['extract'].items():
                try:
                    conf = archiver['archiver']
                    archiver['ext']
                except:
                    continue
                #check if conf is class and implements open, extractall and close methods
                if re.match(r'application/', mime) and inspect.isclass(conf) and hasattr(conf, 'open') and callable(getattr(conf, 'open')) and hasattr(conf, 'extractall') and callable(getattr(conf, 'extractall')) and hasattr(conf, 'close') and callable(getattr(conf, 'close')):
                    self._archivers['extract'][mime] = archiver
                    
    #****************util methods ********************#
    
    def _name_accepted(self, name):
        """
        Validate file name based on self._options['acceptedName'] regular
        expression.
        """
        if self._options['acceptedName']:
            return re.search(self._options['acceptedName'], name)
        return True
    
    def _unique_name(self, dir_, name, suffix = ' copy', check_num=True):
        """
        Return new unique name based on file name and suffix
        """
        
        ext  = ''
        m = re.search(r'\.((tar\.(gz|bz|bz2|z|lzo))|cpio\.gz|ps\.gz|xcf\.(gz|bz2)|[a-z0-9]{1,4})$', name, re.IGNORECASE)
        if m:
            ext  = '.%s' % m.group(1)
            name = name[0:len(name)-len(m.group(0))] 
        
        m = re.search('(%s)(\d*)$' % suffix, name, re.IGNORECASE)
        if check_num and m and m.group(2):
            i = int(m.group(2))
            name = name[0:len(name)-len(m.group(2))]
        else:
            i = 1
            name += suffix

        return self._get_available_name(dir_, name, ext, i)
    
    def _is_hidden(self, stat):
        """
        Check if the file/directory is hidden
        """
        return 'hidden' in stat and stat['hidden']

    def _is_locked(self, stat):
        """
        Check if the file/directory is locked
        """
        return 'locked' in stat and stat['locked']
    
    def _urlize(self, url):
        if re.search("[^/?&=]$", url):
            url += '/'
        return url

    def _relpath(self, path):
        """
        Return file path related to root dir
        """
        return '' if path == self._root else path[len(self._root)+1:]

    def _abspath(self, path):
        """
        Convert path related to root dir into real path
        """
        return self._root if path == self._separator else self._join_path(self._root, path)

    def _path(self, path):
        """
        Return fake path started from root dir.
        Required to show path on client side.
        """
        return self._root_name if path == self._root else self._join_path(self._root_name, self._relpath(path))
     
    def _inpath(self, path, parent):
        """
        Return true if ``path`` is child of ``parent``
        """
        return path == parent or path.startswith('%s%s' % (parent, self._separator))
        
    def _isabs(self, path):
        """
        Check if path is absolute.
        """
        if self._separator =='\\':
            return not re.match(r'([a-zA-Z]+:)?\\$') is None
        return path.startswith(os.sep)
    
    def _get_cache_keys(self):
        """
        Get volume cache key prefixes as a 2-item tuple. Directory keys
        come first, followed by the file keys.
        """
        return ('%selfinder_dirs_cache' % self.id(),
        '%selfinder_stats_cache' % self.id())
        
    #*********************************************************************#
    #*                  API TO BE IMPLEMENTED IN SUB-CLASSES             *#
    #*********************************************************************#

    def _dirname(self, path):
        """
        Return parent directory path
        """
        raise NotImplementedError

    def _basename(self, path):
        """
        Return file name
        """
        raise NotImplementedError

    def _join_path(self, path1, path2):
        """
        Join two paths and return full path. If the latter path is
        absolute, return it.
        """
        raise NotImplementedError

    def _normpath(self, path):
        """
        Return normalized path.
        """
        raise NotImplementedError
    
    #************************* file/dir info *********************# 
    
    def _stat(self, path):
        """
        Return stat for given path.
        Stat contains following fields:
        - (int)    size    file size in b. required
        - (int)    ts      file modification time in unix time. required
        - (string) mime    mimetype. required for folders, others - optionally
        - (bool)   read    read permissions. required
        - (bool)   write   write permissions. required
        - (string) alias   for symlinks - link target path relative to root path. optionally
        - (string) target  for symlinks - link target path. optionally
        
        Must raise an os.error on fail
        """
        raise NotImplementedError

    def _subdirs(self, path):
        """
        Return true if path is dir and has at least one childs directory
        """
        raise NotImplementedError

    def _dimensions(self, path, mime):
        """
        Return object width and height
        Ususaly used for images, but can be realize for video etc...
        """
        raise NotImplementedError
    
    #******************** file/dir content *********************#

    def _mimetype(self, path):
        """
        Attempt to read the file's mimetype
        """
        raise NotImplementedError

    def _scandir(self, path):
        """
        Return files list in directory.
        The '.' and '..' special directories are omitted.
        """
        raise NotImplementedError

    def _fopen(self, path, mode="rb"):
        """
        Open file and return file pointer
        """
        raise NotImplementedError

    def _fclose(self, fp, **kwargs):
        """
        Close opened file
        """
        raise NotImplementedError
    
    def _openimage(self, path):
        """
        Open an image file.
        """
        raise NotImplementedError
    
    def _saveimage(self, im, path, form):
        """
        Save an image file
        """
        raise NotImplementedError
    
    #********************  file/dir manipulations *************************#

    def _mkdir(self, path, mode):
        """
        Create dir and return created dir path or false on failed
        """
        raise NotImplementedError

    def _mkfile(self, path, name):
        """
        Create file and return it's path or false on failed
        """
        raise NotImplementedError

    def _symlink(self, source, target_dir, name):
        """
        Create symlink
        """
        raise NotImplementedError

    def _copy(self, source, target_dir, name):
        """
        Copy file into another file (only inside one volume)
        """
        raise NotImplementedError

    def _move(self, source, target_dir, name):
        """
        Move file into another parent dir.
        Return new file path or raise os.error.
        """
        raise NotImplementedError
    

    def _unlink(self, path):
        """
        Remove file
        """
        raise NotImplementedError

    def _rmdir(self, path):
        """
        Remove dir
        """
        raise NotImplementedError

    def _save(self, fp, dir_, name, **kwargs):
        """
        Create new file and write into it from file pointer.
        Return the new file path
        """
        raise NotImplementedError
    
    def _save_uploaded(self, uploaded_file, dir_, name, **kwargs):
        """
        Save the django UploadedFile object and return its new path
        """
        raise NotImplementedError

    def _get_contents(self, path):
        """
        Get file contents
        """
        raise NotImplementedError

    def _put_contents(self, path, content):
        """
        Write a string to a file
        """
        raise NotImplementedError

    def _extract(self, path, arc):
        """
        Extract files from archive
        """
        raise NotImplementedError

    def _archive(self, dir_, files, name, arc):
        """
        Create archive and return its path
        """
        raise NotImplementedError