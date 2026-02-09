<div align="center">
    <img src="https://img.shields.io/github/last-commit/beak2825/epstein-files-archive" alt="GitHub last commit"/>
    <img src="https://img.shields.io/github/commit-activity/w/beak2825/epstein-files-archive" alt="GitHub commit activity"/>
    <img src="https://img.shields.io/github/contributors/beak2825/epstein-files-archive" alt="GitHub contributors"/>
    <br>
    <img src="https://img.shields.io/badge/Found%20Files%20Deleted:-399-red" alt="Files that were deleted and found (YET)."/>
    <img src="https://img.shields.io/badge/Found%20Files%20Changed:-119-orange" alt="Files that were changed and found (YET)."/>
</div>

# epstein-files-archive
This is not archiving the files themselves, this is only archiving the server responses, useful for checksum and Last-Modified
ETags from justice.gov are made in MD5 format. (Edit: This is sometimes true, only regular files contain the MD5 with a -part at the end, but ZIP files don't match this)

This Python script fetches metadata (HTTP headers) for files from the U.S. Department of Justice (DOJ) Epstein disclosures datasets available at https://www.justice.gov/epstein/doj-disclosures. It processes each dataset sequentially, handling pagination, and saves selected response headers to text files without downloading the actual file contents. It also compiles a universal log of file names with their Last-Modified dates and ETags.


![](https://komarev.com/ghpvc/?username=beak2825-epstein-files-archive&label=REPO+VIEWS)


# Minor Notable Things

They deleted all mentions of "Juan Ruiz Toro"  EFTA00031428 EFTA00009897  
They deleted EFTA00020508 a few days after the media spotlighted it for certain statements of Donald Trump  
They rotated EFTA00001931 EFTA00000531    
They redacted a painting/photo that wasn't a victim EFTA00001225  
They deleted a file that contained statements about Donald Trump's Mar-a-Lago Club in Palm Beach, Florida EFTA00261604  
There's files that added redactions 5 hours they were posted, the old versions are lost media, EFT00156482 EFTA00158898 EFTA00158891 EFTA00151816 EFTA00151209 EFTA00094156 EFTA00081180   
 
https://www.justice.gov/epstein/doj-disclosures/data-set-9-files?page=17
On Data Set 9 it starts to break the pagination, and possibly makes files unlisted (Someone verify, I googled some files and they returned a lot of data, but for a suspected unlisted one it was only 1 result)  


They scribbled then fully redacted a screenshot, and possible Epstein's facebook profile picture is visible EFTA00037168  

More soon.
